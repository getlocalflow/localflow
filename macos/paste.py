"""Text injection: clipboard set + Cmd-V via CGEventPost, changeCount-guarded
restore, secure-input detection, best-effort smart spacing."""
import ctypes
import logging
import threading
import time

from AppKit import NSPasteboard, NSPasteboardTypeString, NSWorkspace
import Quartz

log = logging.getLogger("localflow.paste")

_carbon = ctypes.CDLL(
    "/System/Library/Frameworks/Carbon.framework/Carbon"
)
_carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool

KVK_V = 9  # kVK_ANSI_V


def secure_input_active() -> bool:
    try:
        return bool(_carbon.IsSecureEventInputEnabled())
    except Exception:
        return False


def frontmost_app() -> tuple[str | None, str | None]:
    """(bundle_id, localized_name) of the frontmost app."""
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.bundleIdentifier(), app.localizedName()
    except Exception:
        return None, None


def ax_char_before_cursor() -> str | None:
    """Best-effort: the character to the left of the insertion point, via AX.

    Returns None when the focused element can't be read (common in Electron
    apps / web views) — caller treats that as 'unknown'.
    """
    try:
        from ApplicationServices import (
            AXUIElementCreateSystemWide, AXUIElementCopyAttributeValue,
            kAXFocusedUIElementAttribute, kAXValueAttribute,
            kAXSelectedTextRangeAttribute, AXValueGetValue,
            kAXValueCFRangeType,
        )
        system = AXUIElementCreateSystemWide()
        err, focused = AXUIElementCopyAttributeValue(
            system, kAXFocusedUIElementAttribute, None)
        if err != 0 or focused is None:
            return None
        err, value = AXUIElementCopyAttributeValue(
            focused, kAXValueAttribute, None)
        if err != 0 or not isinstance(value, str):
            return None
        err, rng = AXUIElementCopyAttributeValue(
            focused, kAXSelectedTextRangeAttribute, None)
        if err != 0 or rng is None:
            return None
        ok, cf_range = AXValueGetValue(rng, kAXValueCFRangeType, None)
        if not ok:
            return None
        loc = cf_range.location
        if loc <= 0 or loc > len(value):
            return ""  # start of field
        return value[loc - 1]
    except Exception:
        return None


def smart_space(text: str) -> str:
    """Prepend exactly one space when appending mid-sentence; none at line start."""
    prev = ax_char_before_cursor()
    if prev is None:
        return text  # unknown context: paste as-is
    if prev == "" or prev in "\n\t ":
        return text
    return " " + text


class Paster:
    def __init__(self):
        self.pb = NSPasteboard.generalPasteboard()
        self._saved = None
        self._saved_change = None
        self._our_change = None

    def save_clipboard(self):
        """Snapshot current string contents (skip if huge/absent)."""
        self._saved = None
        self._saved_change = self.pb.changeCount()
        try:
            s = self.pb.stringForType_(NSPasteboardTypeString)
            if s is not None and len(s) <= 2_000_000:
                self._saved = str(s)
        except Exception:
            pass

    def set_clipboard(self, text: str):
        self.pb.clearContents()
        self.pb.setString_forType_(text, NSPasteboardTypeString)
        self._our_change = self.pb.changeCount()

    def send_cmd_v(self):
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        down = Quartz.CGEventCreateKeyboardEvent(src, KVK_V, True)
        up = Quartz.CGEventCreateKeyboardEvent(src, KVK_V, False)
        Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    def restore_clipboard_later(self, delay: float = 0.6):
        """Restore the saved clipboard after `delay`, but only if nobody
        (user or app) touched the pasteboard since our set — the user wins."""
        saved, our_change = self._saved, self._our_change

        def _restore():
            time.sleep(delay)
            try:
                if saved is not None and self.pb.changeCount() == our_change:
                    self.pb.clearContents()
                    self.pb.setString_forType_(saved, NSPasteboardTypeString)
            except Exception:
                pass

        threading.Thread(target=_restore, daemon=True).start()

    def paste(self, text: str) -> str:
        """Full paste flow. Returns 'pasted' | 'secure' (left on clipboard)."""
        if secure_input_active():
            self.set_clipboard(text)  # no restore: user needs it for ⌘V
            log.info("secure input active: text left on clipboard")
            return "secure"
        text = smart_space(text)
        self.save_clipboard()
        self.set_clipboard(text)
        self.send_cmd_v()
        self.restore_clipboard_later()
        return "pasted"

    def copy_only(self, text: str):
        """Put text on the clipboard without pasting (recovery paths)."""
        self.set_clipboard(text)
