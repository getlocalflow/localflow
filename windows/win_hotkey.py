"""Global hotkey via Win32 low-level keyboard hook (WH_KEYBOARD_LL).

The hook runs on its own thread with a message pump. Modifier state is read
with GetAsyncKeyState at event time (Logi-style atomic combos arrive fine).
Import of this module must be safe on any OS; only start() touches Win32.

CONTRACT: on_trigger/on_esc run synchronously inside the system-wide LL hook
(300ms OS timeout; every app's keystrokes wait on them). They must only
schedule work (e.g. root.after) and return within a few ms - never block,
never do I/O.
"""
import logging
import sys
import threading

log = logging.getLogger("localflow.hotkey")

VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win_l": 0x5B, "win_r": 0x5C}
VK_ESCAPE = 0x1B
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104


def combo_matches(vk, mods_down, want_vk, want_mods):
    """True when vk is the wanted key, all wanted modifiers are down, and no
    unrelated modifier (other than shift, the raw-mode overlay) is down."""
    if vk != want_vk:
        return False
    want = set(want_mods)
    if not want.issubset(mods_down):
        return False
    extras = mods_down - want - {"shift"}
    return not extras


class HotkeyListener:
    def __init__(self, on_trigger, on_esc):
        self.on_trigger = on_trigger
        self.on_esc = on_esc
        self._thread = None
        self._thread_id = None
        self._started = threading.Event()
        self._ok = False

    def _mods_down(self, user32):
        down = set()
        for name, code in (("ctrl", VK["ctrl"]), ("alt", VK["alt"]),
                           ("shift", VK["shift"])):
            if user32.GetAsyncKeyState(code) & 0x8000:
                down.add(name)
        if (user32.GetAsyncKeyState(VK["win_l"]) & 0x8000) or \
           (user32.GetAsyncKeyState(VK["win_r"]) & 0x8000):
            down.add("win")
        return down

    def _run(self):
        import ctypes
        import ctypes.wintypes as wt
        from core.config import cfg
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

        LRESULT = ctypes.c_ssize_t
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                             wt.HINSTANCE, wt.DWORD]
        user32.CallNextHookEx.restype = LRESULT
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          wt.WPARAM, wt.LPARAM]
        user32.UnhookWindowsHookEx.restype = wt.BOOL
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.GetMessageW.restype = ctypes.c_int
        kernel32.GetCurrentThreadId.restype = wt.DWORD

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                        ("flags", wt.DWORD), ("time", wt.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        def proc(n_code, w_param, l_param):
            if n_code == 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT))[0]
                vk = kb.vkCode
                try:
                    if vk == VK_ESCAPE:
                        if not self._mods_down(user32):
                            self.on_esc()
                    else:
                        mods = self._mods_down(user32)
                        if combo_matches(vk, mods, cfg.trigger_vk,
                                         list(cfg.trigger_mods)):
                            self.on_trigger("shift" in mods)
                except Exception:
                    log.exception("hotkey callback")
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc_ref = HOOKPROC(proc)   # keep alive or the hook dies
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc_ref, None, 0)
        self._ok = bool(hook)
        self._thread_id = kernel32.GetCurrentThreadId()
        self._started.set()
        if not hook:
            log.error("SetWindowsHookExW failed")
            self._thread_id = None
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if not user32.UnhookWindowsHookEx(hook):
            log.warning("UnhookWindowsHookEx failed")

    def start(self):
        if sys.platform != "win32":
            return False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="hotkey")
        self._thread.start()
        self._started.wait(timeout=5)
        return self._ok

    def stop(self):
        if self._thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
