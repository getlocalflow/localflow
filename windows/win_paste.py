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


def _declare(user32, kernel32):
    """Declare argtypes/restype for the Win32 functions we use. Without these,
    ctypes truncates pointer-sized return values to 32 bits on 64-bit Windows,
    corrupting handles and breaking paste on real hardware."""
    import ctypes
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]


def _open_clipboard(user32):
    """OpenClipboard with retry: clipboard managers/AV hold it transiently."""
    for _ in range(10):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.01)
    return False


def _clipboard_get():
    import ctypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    _declare(user32, kernel32)
    if not _open_clipboard(user32):
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
    _declare(user32, kernel32)
    GMEM_MOVEABLE = 0x0002
    data = text + "\0"
    size = len(data) * ctypes.sizeof(ctypes.c_wchar)
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not h:
        return False
    ptr = kernel32.GlobalLock(h)
    if not ptr:
        kernel32.GlobalFree(h)
        return False
    ctypes.memmove(ptr, ctypes.create_unicode_buffer(data), size)
    kernel32.GlobalUnlock(h)
    if not _open_clipboard(user32):
        kernel32.GlobalFree(h)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            # Per MSDN: on failure the caller retains ownership and must free;
            # on success the system owns the handle and we must NOT free it.
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


# Runs on the tk main thread; 4x10ms sleeps = 40ms, acceptable; move
# off-thread if the latency budget tightens.
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
    def __init__(self):
        # Generation counter: a stale restore thread from a rapid earlier
        # paste must never overwrite a newer paste's clipboard state.
        self._gen = 0

    def copy_only(self, text) -> bool:
        try:
            return _clipboard_set(text)
        except Exception:
            log.exception("copy_only")
            return False

    # UIPI: synthetic input into elevated windows is silently dropped;
    # undetectable here - guide's troubleshooting covers it.
    def paste(self, text) -> bool:
        try:
            self._gen += 1
            gen = self._gen
            prior = _clipboard_get()
            if not _clipboard_set(text):
                return False
            _send_ctrl_v()
            if prior is not None:
                def restore():
                    time.sleep(RESTORE_DELAY_S)
                    try:
                        if self._gen == gen and _clipboard_get() == text:
                            _clipboard_set(prior)
                    except Exception:
                        log.exception("clipboard restore")
                threading.Thread(target=restore, daemon=True).start()
            return True
        except Exception:
            log.exception("paste")
            return False
