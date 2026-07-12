"""Floating pill HUD: capsule, bottom-center, gradient waveform → bouncing-dot
processing → checkmark. All methods must be called on the main thread
(use AppHelper.callAfter).

Motion language: one curve (easeOut, 180-250ms), scale pop for success only,
breathing idle ripple so the pill never looks frozen.
"""
import logging
import math
import time

import objc
from AppKit import (
    NSPanel, NSView, NSColor, NSColorSpace, NSFont, NSTextField, NSImageView,
    NSImage, NSVisualEffectView, NSVisualEffectMaterialHUDWindow,
    NSVisualEffectBlendingModeBehindWindow, NSVisualEffectStateActive,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
    NSBackingStoreBuffered, NSScreen, NSStatusWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSImageSymbolConfiguration, NSTrackingArea,
    NSTrackingMouseEnteredAndExited, NSTrackingActiveAlways,
    NSAnimationContext,
)
from Quartz import CALayer, CATransaction, CABasicAnimation, CAMediaTimingFunction
import Quartz
from Foundation import NSTimer

log = logging.getLogger("localflow.hud")

PILL_W, PILL_H = 300.0, 54.0
BAR_COUNT = 24
BAR_W, BAR_GAP = 5.0, 3.5
BAR_MIN_H, BAR_MAX_H = 5.0, 38.0
BOTTOM_OFFSET = 96.0
MAX_ALPHA = 0.84          # noticeably see-through at rest (light-glass look)
EASE = CAMediaTimingFunction.functionWithName_("easeOut")


def _srgb(nscolor):
    c = nscolor.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
    if c is None:
        c = NSColor.grayColor().colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
    return c


def _cg(nscolor, alpha=None):
    c = _srgb(nscolor)
    return Quartz.CGColorCreateGenericRGB(
        c.redComponent(), c.greenComponent(), c.blueComponent(),
        alpha if alpha is not None else c.alphaComponent())


def _lerp_color(c1, c2, t):
    a, b = _srgb(c1), _srgb(c2)
    return Quartz.CGColorCreateGenericRGB(
        a.redComponent() + (b.redComponent() - a.redComponent()) * t,
        a.greenComponent() + (b.greenComponent() - a.greenComponent()) * t,
        a.blueComponent() + (b.blueComponent() - a.blueComponent()) * t,
        0.92)


class ClickView(NSView):
    """Transparent overlay that reports clicks + hover to the HUD."""

    def initWithHud_(self, hud):
        self = objc.super(ClickView, self).init()
        self._hud = hud
        return self

    def mouseDown_(self, event):
        self._hud.on_click()

    def updateTrackingAreas(self):
        for ta in self.trackingAreas():
            self.removeTrackingArea_(ta)
        ta = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways,
            self, None,
        )
        self.addTrackingArea_(ta)

    def mouseEntered_(self, event):
        self._hud.set_hover(True)

    def mouseExited_(self, event):
        self._hud.set_hover(False)


class HUD:
    """The pill. States: hidden | recording | processing | done | error | info."""

    def __init__(self, on_cancel=None):
        self.on_cancel = on_cancel or (lambda: None)
        self.state = "hidden"
        self.level_source = lambda: 0.0   # set by the daemon (recorder RMS)
        self._bars = []
        self._bar_rgb = []
        self._dots = []
        self._history = [0.0] * (BAR_COUNT // 2 + 1)   # newest level first
        self._heights = [BAR_MIN_H] * BAR_COUNT        # smoothed per-bar
        self._frame = 0
        self._timer = None
        self._rec_started = 0.0
        self._build()

    # -- construction ---------------------------------------------------------

    def _build(self):
        rect = ((0, 0), (PILL_W, PILL_H))
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False,
        )
        self.panel.setLevel_(NSStatusWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self.panel.setHidesOnDeactivate_(False)

        # Waveform-only design: the recording/processing states float bare on
        # screen (no capsule). A small light-glass capsule appears ONLY for
        # text moments (word count / info / errors), which need a surface.
        from AppKit import NSAppearance, NSVisualEffectMaterialPopover
        light = NSAppearance.appearanceNamed_("NSAppearanceNameVibrantLight")
        self.panel.setAppearance_(light)
        self.panel.setHasShadow_(False)   # bars carry their own soft shadows

        content = NSView.alloc().initWithFrame_(rect)
        content.setWantsLayer_(True)
        self.panel.setContentView_(content)
        self.content = content

        self.effect = NSVisualEffectView.alloc().initWithFrame_(rect)
        self.effect.setMaterial_(NSVisualEffectMaterialPopover)
        self.effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        self.effect.setState_(NSVisualEffectStateActive)
        self.effect.setAppearance_(light)
        self.effect.setWantsLayer_(True)
        self.effect.layer().setCornerRadius_(PILL_H / 2.0)
        self.effect.layer().setMasksToBounds_(True)
        self.effect.layer().setBorderWidth_(0.5)
        self.effect.layer().setBorderColor_(
            Quartz.CGColorCreateGenericRGB(1, 1, 1, 0.45))
        self.effect.setHidden_(True)      # only shown for text states
        content.addSubview_(self.effect)
        self.panel.setAlphaValue_(MAX_ALPHA)

        # state tint wash (animated opacity; green on done, amber on error)
        self.tint = CALayer.layer()
        self.tint.setFrame_(((0, 0), (PILL_W, PILL_H)))
        self.tint.setOpacity_(0.0)
        self.effect.layer().addSublayer_(self.tint)

        # pulsing recording dot
        self.dot = self._make_layer()
        self.dot.setBounds_(((0, 0), (8, 8)))
        self.dot.setCornerRadius_(4.0)
        self.dot.setPosition_((22, PILL_H / 2))
        self.dot.setBackgroundColor_(_cg(NSColor.systemRedColor()))
        self._soft_shadow(self.dot, glow=_cg(NSColor.systemRedColor()))

        # waveform bars — teal→violet gradient across the field
        total_w = BAR_COUNT * BAR_W + (BAR_COUNT - 1) * BAR_GAP
        x0 = (PILL_W - total_w) / 2 + 8
        c_from = NSColor.colorWithSRGBRed_green_blue_alpha_(0.45, 0.35, 1.0, 1.0)
        c_to = NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.25, 0.6, 1.0)
        for i in range(BAR_COUNT):
            bar = self._make_layer()
            bar.setBounds_(((0, 0), (BAR_W, BAR_MIN_H)))
            bar.setCornerRadius_(BAR_W / 2)
            bar.setPosition_((x0 + i * (BAR_W + BAR_GAP), PILL_H / 2))
            t = abs(i - (BAR_COUNT - 1) / 2.0) / ((BAR_COUNT - 1) / 2.0)
            color = _lerp_color(c_from, c_to, t)
            bar.setBackgroundColor_(color)
            self._soft_shadow(bar, glow=color)
            self._bars.append(bar)
            # rgb triple for white-flare mixing in _tick
            fa, fb = _srgb(c_from), _srgb(c_to)
            self._bar_rgb.append((
                fa.redComponent() + (fb.redComponent() - fa.redComponent()) * t,
                fa.greenComponent() + (fb.greenComponent() - fa.greenComponent()) * t,
                fa.blueComponent() + (fb.blueComponent() - fa.blueComponent()) * t))
        self._levels = [0.0] * BAR_COUNT

        # processing: three bouncing dots (replaces the stock spinner)
        for i in range(3):
            d = self._make_layer()
            d.setBounds_(((0, 0), (6, 6)))
            d.setCornerRadius_(3.0)
            d.setPosition_((18 + i * 11, PILL_H / 2))
            color = _lerp_color(c_from, c_to, i / 2)
            d.setBackgroundColor_(color)
            self._soft_shadow(d, glow=color)
            d.setHidden_(True)
            self._dots.append(d)

        # caption (timer / mic warning)
        self.caption = NSTextField.labelWithString_("")
        self.caption.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(11, 0.0))
        self.caption.setTextColor_(NSColor.secondaryLabelColor())
        self.caption.setFrame_(((0, 4), (PILL_W - 14, 14)))
        self.caption.setAlignment_(2)  # right
        self.caption.setWantsLayer_(True)
        self._soft_shadow(self.caption.layer())
        self.content.addSubview_(self.caption)

        # center message (errors/info/success text)
        self.message = NSTextField.labelWithString_("")
        self.message.setFont_(NSFont.systemFontOfSize_(12))
        self.message.setTextColor_(NSColor.labelColor())
        self.message.setFrame_(((52, (PILL_H - 16) / 2), (PILL_W - 66, 16)))
        self.message.setHidden_(True)
        self.message.setWantsLayer_(True)
        self._soft_shadow(self.message.layer())
        self.content.addSubview_(self.message)

        # status icon (checkmark / warning / mic-slash)
        self.icon = NSImageView.alloc().initWithFrame_(
            ((14, (PILL_H - 20) / 2), (20, 20)))
        self.icon.setWantsLayer_(True)
        self.icon.setHidden_(True)
        self.content.addSubview_(self.icon)

        # RAW tag
        self.raw_tag = NSTextField.labelWithString_("RAW")
        self.raw_tag.setFont_(NSFont.boldSystemFontOfSize_(9))
        self.raw_tag.setTextColor_(NSColor.systemOrangeColor())
        self.raw_tag.setFrame_(((PILL_W - 38, PILL_H - 16), (30, 12)))
        self.raw_tag.setHidden_(True)
        self.raw_tag.setWantsLayer_(True)
        self._soft_shadow(self.raw_tag.layer())
        self.content.addSubview_(self.raw_tag)

        # cancel ✕ (hover affordance)
        self.x_label = NSTextField.labelWithString_("✕")
        self.x_label.setFont_(NSFont.systemFontOfSize_(12))
        self.x_label.setTextColor_(NSColor.secondaryLabelColor())
        self.x_label.setFrame_(((PILL_W - 24, (PILL_H - 16) / 2), (16, 16)))
        self.x_label.setHidden_(True)
        self.content.addSubview_(self.x_label)

        # click/hover overlay on top
        self.clicks = ClickView.alloc().initWithHud_(self)
        self.clicks.setFrame_(rect)
        self.content.addSubview_(self.clicks)

    def _make_layer(self):
        layer = CALayer.layer()
        self.content.layer().addSublayer_(layer)
        return layer

    @staticmethod
    def _soft_shadow(layer, glow=None):
        """Glow (colored) or soft shadow so bare elements read on anything."""
        if glow is not None:
            layer.setShadowColor_(glow)
            layer.setShadowOpacity_(0.9)
            layer.setShadowRadius_(4.0)
            layer.setShadowOffset_((0, 0))
        else:
            layer.setShadowColor_(Quartz.CGColorCreateGenericRGB(0, 0, 0, 1))
            layer.setShadowOpacity_(0.35)
            layer.setShadowRadius_(1.5)
            layer.setShadowOffset_((0, -0.5))

    def _symbol(self, name, color):
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if img is None:
            return None
        conf = NSImageSymbolConfiguration.configurationWithPointSize_weight_(16, 0.3)
        img = img.imageWithSymbolConfiguration_(conf)
        self.icon.setContentTintColor_(color)
        return img

    # -- positioning & entrance/exit motion ------------------------------------

    def _place(self):
        """Bottom-center of the screen with the mouse cursor (attention proxy)."""
        from AppKit import NSEvent
        mouse = NSEvent.mouseLocation()
        screen = None
        for s in NSScreen.screens():
            f = s.frame()
            if (f.origin.x <= mouse.x <= f.origin.x + f.size.width
                    and f.origin.y <= mouse.y <= f.origin.y + f.size.height):
                screen = s
                break
        screen = screen or NSScreen.mainScreen()
        f = screen.visibleFrame()
        x = f.origin.x + (f.size.width - PILL_W) / 2
        y = f.origin.y + BOTTOM_OFFSET
        self.panel.setFrameOrigin_((x, y))
        self._home_y = y

    def _animate_in(self):
        """Slide up 10pt + fade in, 180ms easeOut."""
        f = self.panel.frame()
        self.panel.setAlphaValue_(0.0)
        self.panel.setFrameOrigin_((f.origin.x, self._home_y - 10))
        self.panel.orderFrontRegardless()
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.18)
        self.panel.animator().setAlphaValue_(MAX_ALPHA)
        self.panel.animator().setFrameOrigin_((f.origin.x, self._home_y))
        NSAnimationContext.endGrouping()

    def _animate_out(self):
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.25)
        self.panel.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.3, False, lambda t: self.panel.orderOut_(None))

    # -- waveform animation (30fps NSTimer) --------------------------------------

    def _tick(self, _timer=None):
        if self.state != "recording":
            return
        level = min(1.0, (self.level_source() * 22.0) ** 0.85)  # RMS -> 0..1, punchy
        self._frame += 1
        if self._frame % 2 == 0:            # history scrolls at ~30Hz
            self._history.insert(0, level)
            self._history.pop()
        now = time.time()
        center = (BAR_COUNT - 1) / 2.0
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        for i, bar in enumerate(self._bars):
            d = int(abs(i - center))        # distance from center: newest audio
            lv = self._history[min(d, len(self._history) - 1)]
            # idle ripple radiates outward from the center
            idle = (1.0 + math.sin(now * 3.0 - d * 0.7)) * 1.8
            target = BAR_MIN_H + idle + (BAR_MAX_H - BAR_MIN_H - idle) * lv
            # liquid smoothing toward the target height
            h = self._heights[i] + (target - self._heights[i]) * 0.35
            self._heights[i] = h
            bar.setBounds_(((0, 0), (BAR_W, h)))
            # white flare follows the smoothed height
            frac = max(0.0, (h - BAR_MIN_H) / (BAR_MAX_H - BAR_MIN_H))
            r, g, b = self._bar_rgb[i]
            mix = 0.75 * frac
            bar.setBackgroundColor_(Quartz.CGColorCreateGenericRGB(
                r + (1.0 - r) * mix, g + (1.0 - g) * mix,
                b + (1.0 - b) * mix, 0.95))
        CATransaction.commit()
        # timer caption after 10s; amber near the cap
        el = now - self._rec_started
        if el >= 10.0 and not self.caption.stringValue().startswith("Not"):
            self.caption.setStringValue_(f"{int(el // 60)}:{int(el % 60):02d}")
            from config import cfg
            if el >= cfg.warn_recording_s:
                self.caption.setTextColor_(NSColor.systemOrangeColor())

    def _start_timer(self):
        self._stop_timer()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1.0 / 60.0, True, self._tick)

    def _stop_timer(self):
        if self._timer:
            self._timer.invalidate()
            self._timer = None

    # -- reusable micro-animations --------------------------------------------------

    def _pulse_dot(self):
        anim = CABasicAnimation.animationWithKeyPath_("opacity")
        anim.setFromValue_(1.0)
        anim.setToValue_(0.35)
        anim.setDuration_(0.8)
        anim.setAutoreverses_(True)
        anim.setRepeatCount_(1e9)
        anim.setTimingFunction_(EASE)
        self.dot.addAnimation_forKey_(anim, "pulse")

    def _bounce_dots(self):
        for i, d in enumerate(self._dots):
            d.setHidden_(False)
            anim = CABasicAnimation.animationWithKeyPath_("position.y")
            anim.setFromValue_(PILL_H / 2 - 3)
            anim.setToValue_(PILL_H / 2 + 3)
            anim.setDuration_(0.38)
            anim.setAutoreverses_(True)
            anim.setRepeatCount_(1e9)
            anim.setTimingFunction_(EASE)
            anim.setBeginTime_(Quartz.CACurrentMediaTime() + i * 0.13)
            d.addAnimation_forKey_(anim, "bounce")

    def _hide_dots(self):
        for d in self._dots:
            d.removeAllAnimations()
            d.setHidden_(True)

    def _wash(self, nscolor, opacity=0.14):
        """Animated state tint on the capsule."""
        self.tint.setBackgroundColor_(_cg(nscolor, 1.0))
        anim = CABasicAnimation.animationWithKeyPath_("opacity")
        anim.setFromValue_(0.0)
        anim.setToValue_(opacity)
        anim.setDuration_(0.2)
        anim.setTimingFunction_(EASE)
        self.tint.addAnimation_forKey_(anim, "wash")
        self.tint.setOpacity_(opacity)

    def _clear_wash(self):
        self.tint.removeAllAnimations()
        self.tint.setOpacity_(0.0)

    # -- public API (main thread only) ------------------------------------------

    def show_recording(self, raw: bool = False):
        self.state = "recording"
        self._reset_width()
        self.effect.setHidden_(True)
        self._clear_wash()
        self._hide_dots()
        self._rec_started = time.time()
        self._levels = [0.0] * BAR_COUNT
        self._history = [0.0] * (BAR_COUNT // 2 + 1)
        self._heights = [BAR_MIN_H] * BAR_COUNT
        self._set_bars_hidden(False)
        self.dot.setHidden_(False)
        self._pulse_dot()
        self.icon.setHidden_(True)
        self.message.setHidden_(True)
        self.raw_tag.setHidden_(not raw)
        self.x_label.setHidden_(True)
        self.caption.setStringValue_("")
        self.caption.setTextColor_(NSColor.secondaryLabelColor())
        self._place()
        self._animate_in()
        self._start_timer()

    def mic_warning(self, warn: bool):
        if self.state != "recording":
            return
        self.caption.setStringValue_(
            "Not hearing anything — check your mic" if warn else "")
        self.caption.setTextColor_(
            NSColor.systemOrangeColor() if warn else NSColor.secondaryLabelColor())

    def hold_cancel_hint(self, active: bool):
        if self.state != "recording":
            return
        self.message.setStringValue_("Release to cancel")
        self.message.setHidden_(not active)
        self._set_bars_hidden(active)
        self.dot.setHidden_(active)

    def show_processing(self, label: str = "Polishing…"):
        self.state = "processing"
        self._stop_timer()
        self._collapse_bars()
        self.dot.removeAllAnimations()
        self.dot.setHidden_(True)
        self.raw_tag.setHidden_(True)
        self.caption.setStringValue_("")
        self.message.setStringValue_(label)
        self.message.setHidden_(False)
        self._bounce_dots()

    def _collapse_bars(self):
        """Bars sink to minimum then hide — a 150ms 'gather' beat."""
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.15)
        for bar in self._bars:
            bar.setBounds_(((0, 0), (BAR_W, BAR_MIN_H)))
        CATransaction.commit()
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.15, False, lambda t: self._set_bars_hidden(True)
            if self.state != "recording" else None)

    def show_done(self, words: int, note: str = ""):
        self.state = "done"
        self._finish_common()
        self.icon.setImage_(self._symbol("checkmark.circle.fill",
                                         NSColor.systemGreenColor()))
        self.icon.setHidden_(False)
        text = f"{words} word{'s' if words != 1 else ''}"
        if note:
            text += f" · {note}"
        self._fit_message(text)
        self._wash(NSColor.systemGreenColor(), 0.12)
        self._pop_icon()
        self._dismiss_after(1.2)

    def show_info(self, text: str, dismiss_s: float = 1.5):
        self.state = "info"
        self._finish_common()
        self.icon.setImage_(self._symbol("mic.slash", NSColor.secondaryLabelColor()))
        self.icon.setHidden_(False)
        self._place()
        self._fit_message(text)
        if not self.panel.isVisible() or self.panel.alphaValue() < MAX_ALPHA:
            self._animate_in()
        self._dismiss_after(dismiss_s)

    def show_error(self, text: str, dismiss_s: float = 4.0):
        self.state = "error"
        self._finish_common()
        self.icon.setImage_(self._symbol("exclamationmark.triangle.fill",
                                         NSColor.systemOrangeColor()))
        self.icon.setHidden_(False)
        self._place()
        self._fit_message(text.lstrip("⚠ "))
        self._wash(NSColor.systemOrangeColor(), 0.14)
        if not self.panel.isVisible() or self.panel.alphaValue() < MAX_ALPHA:
            self._animate_in()
        self._dismiss_after(dismiss_s)

    def hide(self):
        self.state = "hidden"
        self._stop_timer()
        self._hide_dots()
        self._animate_out()

    # -- internals ---------------------------------------------------------------

    def _finish_common(self):
        self._stop_timer()
        self._set_bars_hidden(True)
        self._hide_dots()
        self.dot.removeAllAnimations()
        self.dot.setHidden_(True)
        self.raw_tag.setHidden_(True)
        self.x_label.setHidden_(True)
        self.caption.setStringValue_("")

    def _set_bars_hidden(self, hidden: bool):
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        for bar in self._bars:
            bar.setHidden_(hidden)
        CATransaction.commit()

    def _fit_message(self, text: str):
        """Resize the pill to fit a message state (up to 420pt), re-center."""
        self.effect.setHidden_(False)
        self.message.setStringValue_(text)
        self.message.sizeToFit()
        needed = min(420.0, 36 + self.message.frame().size.width + 28)
        w = max(PILL_W, needed)
        f = self.panel.frame()
        self.panel.setFrame_display_(
            ((f.origin.x - (w - f.size.width) / 2, f.origin.y), (w, PILL_H)), True)
        self.effect.setFrame_(((0, 0), (w, PILL_H)))
        self.clicks.setFrame_(((0, 0), (w, PILL_H)))
        self.tint.setFrame_(((0, 0), (w, PILL_H)))
        self.message.setFrame_(((36, (PILL_H - 16) / 2), (w - 50, 16)))
        self.message.setHidden_(False)

    def _reset_width(self):
        f = self.panel.frame()
        if f.size.width != PILL_W:
            self.panel.setFrame_display_(
                ((f.origin.x + (f.size.width - PILL_W) / 2, f.origin.y),
                 (PILL_W, PILL_H)), False)
            self.effect.setFrame_(((0, 0), (PILL_W, PILL_H)))
            self.clicks.setFrame_(((0, 0), (PILL_W, PILL_H)))
            self.tint.setFrame_(((0, 0), (PILL_W, PILL_H)))

    def _pop_icon(self):
        anim = CABasicAnimation.animationWithKeyPath_("transform.scale")
        anim.setFromValue_(1.0)
        anim.setToValue_(1.15)
        anim.setDuration_(0.125)
        anim.setAutoreverses_(True)
        anim.setTimingFunction_(EASE)
        if self.icon.layer():
            self.icon.layer().addAnimation_forKey_(anim, "pop")

    def _dismiss_after(self, seconds: float):
        token = time.monotonic()
        self._dismiss_token = token

        def _later():
            if getattr(self, "_dismiss_token", None) == token and \
                    self.state in ("done", "info", "error", "cancelled"):
                self.hide()

        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            seconds, False, lambda t: _later())

    # -- clicks / hover ------------------------------------------------------------

    def on_click(self):
        if self.state in ("recording", "processing"):
            self.on_cancel()
        else:
            self.hide()

    def set_hover(self, hovering: bool):
        if self.state == "recording":
            self.x_label.setHidden_(not hovering)
