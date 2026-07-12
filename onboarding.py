"""First-run setup & Fix Permissions window.

One window, checklist style: permission rows flip ✕→✓ live as you grant them,
then a live F13-detection row, then a test-dictation field. The same window
(minus the test field) serves as "Fix Permissions…".
"""
import logging
import subprocess
import time

from AppKit import (
    NSWindow, NSTextField, NSButton, NSColor, NSFont, NSScreen,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable, NSBackingStoreBuffered,
    NSMakeRect,
)
from ApplicationServices import AXIsProcessTrusted
from Foundation import NSTimer

from config import cfg

log = logging.getLogger("localflow.onboarding")

_window = None  # keep a reference so it isn't GC'd

SETTINGS_URLS = {
    "mic": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "ax": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "input": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
}


def mic_ok() -> bool:
    try:
        import sounddevice as sd
        devs = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
        return len(devs) > 0
    except Exception:
        return False


def ax_ok() -> bool:
    try:
        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def _label(text, frame, size=13, bold=False, color=None):
    l = NSTextField.labelWithString_(text)
    l.setFrame_(frame)
    l.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
               else NSFont.systemFontOfSize_(size))
    if color:
        l.setTextColor_(color)
    return l


def _open_settings(pane):
    subprocess.Popen(["open", SETTINGS_URLS[pane]])


class SetupWindow:
    """Checklist window; rows poll live and flip ✕→✓."""

    def __init__(self, daemon=None, full=True):
        self.daemon = daemon
        self.full = full            # full = onboarding; False = fix-permissions only
        self.f13_seen = False
        self.test_done = False
        h = 430 if full else 260
        self.win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 500, h),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False,
        )
        self.win.setTitle_("LocalFlow Setup" if full else "LocalFlow Permissions")
        self.win.setReleasedWhenClosed_(False)
        v = self.win.contentView()
        y = h - 46

        v.addSubview_(_label("LocalFlow — dictation that stays on your Mac",
                             NSMakeRect(20, y, 460, 20), 15, True))
        y -= 24
        v.addSubview_(_label("Everything runs locally. Nothing leaves this machine.",
                             NSMakeRect(20, y, 460, 16), 11,
                             color=NSColor.secondaryLabelColor()))
        y -= 36

        self.rows = {}
        for key, title, hint in [
            ("mic", "Microphone", "plug in / allow your mic"),
            ("ax", "Accessibility", "lets LocalFlow paste text for you"),
            ("input", "Input Monitoring", "lets LocalFlow hear the mouse button"),
        ]:
            mark = _label("✕", NSMakeRect(24, y, 20, 18), 14, True,
                          NSColor.systemRedColor())
            name = _label(f"{title} — {hint}", NSMakeRect(48, y, 320, 18), 12)
            btn = NSButton.buttonWithTitle_target_action_("Open Settings", None, None)
            btn.setFrame_(NSMakeRect(376, y - 4, 110, 26))
            btn.setTag_({"mic": 1, "ax": 2, "input": 3}[key])
            btn.setTarget_(self._helper())
            btn.setAction_(b"openPane:")
            for w in (mark, name, btn):
                v.addSubview_(w)
            self.rows[key] = (mark, btn)
            y -= 34

        if full:
            y -= 8
            v.addSubview_(_label("Mouse button", NSMakeRect(20, y, 460, 18), 13, True))
            y -= 22
            v.addSubview_(_label(
                "In Logi Options+ assign your button to Keyboard shortcut ⌃⌥⌘D,",
                NSMakeRect(20, y, 460, 16), 11))
            y -= 16
            v.addSubview_(_label(
                "then press it — LocalFlow is listening right now…",
                NSMakeRect(20, y, 460, 16), 11))
            y -= 26
            self.f13_mark = _label("● waiting for your mouse button",
                                   NSMakeRect(24, y, 300, 18), 12, True,
                                   NSColor.systemOrangeColor())
            v.addSubview_(self.f13_mark)
            logi = NSButton.buttonWithTitle_target_action_("Open Logi Options+",
                                                           None, None)
            logi.setFrame_(NSMakeRect(340, y - 4, 146, 26))
            logi.setTag_(4)
            logi.setTarget_(self._helper())
            logi.setAction_(b"openPane:")
            v.addSubview_(logi)
            y -= 40

            v.addSubview_(_label("Test dictation", NSMakeRect(20, y, 460, 18), 13, True))
            y -= 22
            v.addSubview_(_label(
                "Click the field, press your button, speak, press again.",
                NSMakeRect(20, y, 460, 16), 11))
            y -= 34
            self.test_field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(20, y, 460, 26))
            self.test_field.setPlaceholderString_("Your words will appear here…")
            v.addSubview_(self.test_field)
            y -= 30
            self.result_label = _label("", NSMakeRect(20, y, 460, 16), 11,
                                       color=NSColor.systemGreenColor())
            v.addSubview_(self.result_label)

        # live polling
        self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1.0, True, self._poll)
        self._poll(None)

        # center + show
        screen = NSScreen.mainScreen().visibleFrame()
        self.win.setFrameOrigin_((
            screen.origin.x + (screen.size.width - 500) / 2,
            screen.origin.y + (screen.size.height - h) / 2 + 80,
        ))
        self.win.makeKeyAndOrderFront_(None)
        try:
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass

    def _helper(self):
        if not hasattr(self, "_helper_obj"):
            import objc
            from AppKit import NSObject

            class _Btn(NSObject):
                def openPane_(inner, sender):  # noqa: N802
                    tag = sender.tag()
                    if tag == 1:
                        _open_settings("mic")
                    elif tag == 2:
                        _open_settings("ax")
                    elif tag == 3:
                        _open_settings("input")
                    elif tag == 4:
                        if subprocess.run(["open", "-a", "Logi Options+"],
                                          capture_output=True).returncode != 0:
                            subprocess.Popen(
                                ["open", "https://www.logitech.com/en-us/software/logi-options-plus.html"])

            self._helper_obj = _Btn.alloc().init()
        return self._helper_obj

    def _set_row(self, key, ok):
        mark, btn = self.rows[key]
        mark.setStringValue_("✓" if ok else "✕")
        mark.setTextColor_(NSColor.systemGreenColor() if ok
                           else NSColor.systemRedColor())
        btn.setHidden_(ok)

    def _poll(self, _timer):
        self._set_row("mic", mic_ok())
        self._set_row("ax", ax_ok())
        listener_ok = bool(self.daemon and self.daemon.listener_running)
        self._set_row("input", listener_ok)
        if self.full and self.daemon:
            if not self.f13_seen and \
                    time.monotonic() - self.daemon.last_event_t < 3.0 and \
                    self.daemon.last_event_t > 0:
                self.f13_seen = True
                self.f13_mark.setStringValue_("✓ Detected!")
                self.f13_mark.setTextColor_(NSColor.systemGreenColor())
            if not self.test_done and self.test_field.stringValue():
                self.test_done = True
                import pipeline
                avg = pipeline.average_latency(1)
                note = f" — {avg/1000:.1f}s from stop to text" if avg else ""
                self.result_label.setStringValue_(f"✓ You're ready{note}. "
                                                  "Side button = start/stop · "
                                                  "⌥+button = verbatim · Esc = cancel")

    def close(self):
        if self.timer:
            self.timer.invalidate()
        self.win.close()


def show_onboarding(daemon):
    global _window
    _window = SetupWindow(daemon, full=True)


def show_permissions_window(daemon=None):
    global _window
    _window = SetupWindow(daemon, full=False)
