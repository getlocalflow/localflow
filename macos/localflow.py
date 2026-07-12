#!/usr/bin/env python3
"""LocalFlow — fully local, private voice dictation.

Press Ctrl+Option+Cmd+D (or a mouse button mapped to it), speak, press again:
clean punctuated text lands at your cursor. Nothing leaves this Mac.

Run:  ./venv/bin/python3 localflow.py
"""
import logging
import sys
from logging.handlers import RotatingFileHandler

from core.config import cfg, LOG_DIR, DAEMON_LOG, CONFIG_PATH


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(DAEMON_LOG, maxBytes=2_000_000, backupCount=2)]
    if sys.stdout.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def request_permissions(log):
    """Trigger the native macOS permission prompts so TCC registers the
    correct process identity (manual list-adds often miss)."""
    try:
        from ApplicationServices import (
            AXIsProcessTrusted, AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        if not AXIsProcessTrusted():
            log.info("requesting Accessibility permission (prompt)")
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        log.exception("accessibility request")
    try:
        import ctypes
        iokit = ctypes.CDLL(
            "/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDCheckAccess.restype = ctypes.c_int
        iokit.IOHIDRequestAccess.restype = ctypes.c_bool
        KIOHID_LISTEN = 1  # kIOHIDRequestTypeListenEvent
        if iokit.IOHIDCheckAccess(KIOHID_LISTEN) != 0:  # 0 = granted
            log.info("requesting Input Monitoring permission (prompt)")
            iokit.IOHIDRequestAccess(KIOHID_LISTEN)
    except Exception:
        log.exception("input monitoring request")


def main():
    setup_logging()
    log = logging.getLogger("localflow")
    log.info("LocalFlow starting (model=%s)", cfg.model)
    request_permissions(log)

    # AppKit objects must be created on the main thread, before rumps.run
    from macos.hud import HUD
    from macos.statemachine import Daemon
    from macos.menubar import LocalFlowApp

    hud = HUD()
    daemon = Daemon(hud)
    app = LocalFlowApp(daemon, hud)

    listener_ok = daemon.start_listener()
    if not listener_ok:
        log.error("keyboard listener failed — Input Monitoring permission?")
        app.set_attention(True)
        daemon.status_note = "needs Input Monitoring permission"

    first_run = not CONFIG_PATH.exists()
    if first_run:
        cfg.set("sounds", True)  # writes config.toml → marks first run done
        from macos.onboarding import show_onboarding
        from PyObjCTools import AppHelper
        AppHelper.callAfter(show_onboarding, daemon)

    app.run()


if __name__ == "__main__":
    main()
