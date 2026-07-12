"""LocalFlow for Windows - entry point."""
import logging
import sys
from logging.handlers import RotatingFileHandler

from core.config import cfg, LOG_DIR, DAEMON_LOG


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(DAEMON_LOG, maxBytes=2_000_000,
                                    backupCount=2)]
    if sys.stdout and sys.stdout.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=handlers)


def already_running() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW(None, False, "Local\\LocalFlowSingleton")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def main():
    setup_logging()
    log = logging.getLogger("localflow")
    if already_running():
        log.info("another LocalFlow instance is running; exiting")
        return
    log.info("LocalFlow (Windows) starting, model=%s", cfg.model)

    import tkinter as tk
    from windows.win_onboarding import first_run_setup, ensure_model
    from windows.win_hud import WinHUD
    from windows.win_paste import WinPaster
    from windows.win_tray import Tray
    from windows.win_hotkey import HotkeyListener
    from windows.statemachine_win import WinDaemon, WinSounds

    root = tk.Tk()
    root.withdraw()
    try:
        # ui() marshals root.after from worker threads; that is only safe on
        # threaded Tcl builds (standard for python.org Windows Python).
        threaded = root.tk.eval("set tcl_platform(threaded)")
        log.info("Tcl threaded=%s", threaded)
        if str(threaded) != "1":
            log.error("non-threaded Tcl build: cross-thread UI updates unsafe")
    except Exception:
        log.exception("tcl threaded check")
    first_run_setup()
    if not ensure_model(root):
        return

    hud = WinHUD(root)
    daemon = WinDaemon(root, hud, WinPaster(), sounds=WinSounds())

    def quit_app():
        tray.stop()
        listener.stop()
        root.after(0, root.destroy)

    tray = Tray(daemon, on_quit=quit_app)
    tray.start()
    # tray color follows daemon state; hop to the tk thread since
    # _set_state can fire from the ASR worker thread's ui() completions
    daemon.on_state_change = lambda s: root.after(0, tray.set_state, s)

    listener = HotkeyListener(on_trigger=daemon.on_trigger,
                              on_esc=daemon.on_esc)
    if not listener.start():
        log.error("keyboard hook failed")
        daemon.status_note = "hotkey unavailable - restart LocalFlow"
        tray.set_state("attention")

    root.mainloop()


if __name__ == "__main__":
    main()
