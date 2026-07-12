"""Paste at cursor: clipboard set + synthetic Ctrl+V (keybd_event) +
clipboard restore. Import-safe on any OS; Win32 only touched in methods."""
import logging
import sys
import threading
import time

log = logging.getLogger("localflow.paste")

VK_CONTROL, VK_V = 0x11, 0x56
CF_UNICODETEXT = 13
KEYEVENTF_KEYUP = 0x0002
RESTORE_DELAY_S = 0.6


def build_key_sequence():
    return [(VK_CONTROL, False), (VK_V, False), (VK_V, True), (VK_CONTROL, True)]


def _clipboard_get():
    import ctypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if not user32.OpenClipboard(None):
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        try:
            return ctypes.c_wchar_p(ptr).value
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def _clipboard_set(text):
    import ctypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    GMEM_MOVEABLE = 0x0002
    data = text + "\0"
    size = len(data) * ctypes.sizeof(ctypes.c_wchar)
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    ptr = kernel32.GlobalLock(h)
    ctypes.memmove(ptr, ctypes.create_unicode_buffer(data), size)
    kernel32.GlobalUnlock(h)
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        return bool(user32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        user32.CloseClipboard()


def _send_ctrl_v():
    import ctypes
    user32 = ctypes.windll.user32
    for vk, up in build_key_sequence():
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, 0)
        time.sleep(0.01)


def foreground_app():
    if sys.platform != "win32":
        return None, None
    import ctypes
    import psutil
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    try:
        exe = psutil.Process(pid.value).name().lower()
    except Exception:
        exe = None
    return exe, buf.value or None


class WinPaster:
    def copy_only(self, text) -> bool:
        try:
            return _clipboard_set(text)
        except Exception:
            log.exception("copy_only")
            return False

    def paste(self, text) -> bool:
        try:
            prior = _clipboard_get()
            if not _clipboard_set(text):
                return False
            _send_ctrl_v()
            if prior is not None:
                def restore():
                    time.sleep(RESTORE_DELAY_S)
                    try:
                        if _clipboard_get() == text:
                            _clipboard_set(prior)
                    except Exception:
                        log.exception("clipboard restore")
                threading.Thread(target=restore, daemon=True).start()
            return True
        except Exception:
            log.exception("paste")
            return False
