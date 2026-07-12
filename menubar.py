"""Menu-bar app (rumps): icon states, menu, sleep/wake observers."""
import logging
import subprocess
import time

import objc
import rumps
from AppKit import NSWorkspace, NSObject
from Foundation import NSTimer

import pipeline
from pathlib import Path

from config import cfg, DICTIONARY_PATH, HISTORY_DIR, ROOT

log = logging.getLogger("localflow.menubar")

ICONS = {"idle": "🎤", "recording": "🔴", "processing": "🟠",
         "paused": "⏸️", "attention": "⚠️"}

PLIST = "com.localflow.daemon"
PLIST_PATH = str(Path.home() / "Library" / "LaunchAgents" / f"{PLIST}.plist")


class WakeObserver(NSObject):
    def initWithDaemon_(self, daemon):
        self = objc.super(WakeObserver, self).init()
        self._daemon = daemon
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        nc.addObserver_selector_name_object_(
            self, b"willSleep:", "NSWorkspaceWillSleepNotification", None)
        nc.addObserver_selector_name_object_(
            self, b"didWake:", "NSWorkspaceDidWakeNotification", None)
        return self

    def willSleep_(self, note):
        self._daemon.on_will_sleep()

    def didWake_(self, note):
        self._daemon.on_did_wake()


class LocalFlowApp(rumps.App):
    def __init__(self, daemon, hud):
        super().__init__("LocalFlow", title=ICONS["idle"], quit_button=None)
        self.daemon = daemon
        self.hud = hud
        daemon.on_state_change = self._state_changed
        self._attention = False
        self._observer = WakeObserver.alloc().initWithDaemon_(daemon)

        self.status_item = rumps.MenuItem("LocalFlow — Ready")
        self.status_item.set_callback(None)
        self.pause_item = rumps.MenuItem("Pause Listening", callback=self.on_pause)
        self.copy_last_item = rumps.MenuItem("Copy Last Transcript",
                                             callback=self.on_copy_last)
        self.recent_menu = rumps.MenuItem("Recent Transcripts")
        self.retry_item = rumps.MenuItem("Retry Last Recording",
                                         callback=self.on_retry)
        self.raw_item = rumps.MenuItem("Raw Mode by Default",
                                       callback=self.on_raw_default)
        self.raw_item.state = cfg.raw_by_default
        self.sounds_item = rumps.MenuItem("Sounds", callback=self.on_sounds)
        self.sounds_item.state = cfg.sounds
        self.dict_item = rumps.MenuItem("Dictionary…", callback=self.on_dictionary)
        self.perms_item = rumps.MenuItem("Fix Permissions…", callback=self.on_perms)
        self.login_item = rumps.MenuItem("Start at Login", callback=self.on_login)
        self.login_item.state = True
        self.about_item = rumps.MenuItem("About LocalFlow", callback=self.on_about)
        self.setup_item = rumps.MenuItem("Run Setup Again", callback=self.on_setup)
        self.quit_item = rumps.MenuItem("Quit LocalFlow", callback=self.on_quit)

        self.menu = [
            self.status_item, None,
            self.pause_item, self.copy_last_item, self.recent_menu,
            self.retry_item, self.perms_item, None,
            self.raw_item, self.sounds_item, self.dict_item, None,
            self.login_item, self.about_item, self.setup_item, self.quit_item,
        ]
        self._rebuild_recent()

        # heartbeat: chunk flush / mic warn / auto-stop / config reload
        self._tick_timer = rumps.Timer(self.on_tick, 0.5)
        self._tick_timer.start()
        # Ollama keep-warm (no-op unless llm_enabled) + immediate pre-warm
        self._warm_timer = rumps.Timer(lambda _: pipeline.ollama_ping(), 240)
        self._warm_timer.start()
        import threading
        threading.Thread(target=pipeline.ollama_ping, daemon=True).start()

    # ---- state → icon/status -----------------------------------------------------

    def _state_changed(self, state):
        def _apply():
            icon = ICONS.get(state, ICONS["idle"])
            if self._attention and state == "idle":
                icon = ICONS["attention"]
            self.title = icon
            labels = {"idle": "LocalFlow — Ready",
                      "recording": "LocalFlow — Recording…",
                      "processing": "LocalFlow — Processing…",
                      "paused": "LocalFlow — Paused"}
            extra = f"  ({self.daemon.status_note})" if self.daemon.status_note else ""
            self.status_item.title = labels.get(state, "LocalFlow") + extra
            self.pause_item.title = ("Resume Listening" if state == "paused"
                                     else "Pause Listening")
            self.retry_item.title = "Retry Last Recording"
            if state == "idle":
                self._rebuild_recent()
        # rumps runs on the main thread; state changes may arrive from others
        from PyObjCTools import AppHelper
        AppHelper.callAfter(_apply)

    def set_attention(self, on: bool):
        self._attention = on
        self._state_changed(self.daemon.state)

    # ---- menu actions ---------------------------------------------------------------

    def on_tick(self, _):
        self.daemon.tick()
        if self.daemon.state == "recording" and self.daemon.session:
            el = self.daemon.session.rec.elapsed()
            self.status_item.title = f"LocalFlow — Recording… {int(el//60)}:{int(el%60):02d}"

    def on_pause(self, _):
        paused = self.daemon.toggle_pause()
        log.info("paused" if paused else "resumed")

    def on_copy_last(self, _):
        recents = pipeline.recent_history(1)
        if recents:
            self.daemon.paster.copy_only(recents[0]["text"])
            rumps.notification("LocalFlow", "", "Last transcript copied.")

    def _rebuild_recent(self):
        try:
            self.recent_menu.clear()
        except Exception:
            pass
        recents = pipeline.recent_history(5)
        if not recents:
            item = rumps.MenuItem("(empty)")
            item.set_callback(None)
            self.recent_menu.add(item)
        for r in recents:
            words = r["text"].split()
            label = " ".join(words[:6]) + ("…" if len(words) > 6 else "")
            age_min = int((time.time() - r["mtime"]) / 60)
            age = f"{age_min}m" if age_min < 60 else f"{age_min // 60}h"
            item = rumps.MenuItem(f"{label} · {age} ago")
            item.set_callback(self._make_copy_cb(r["text"]))
            self.recent_menu.add(item)
        self.recent_menu.add(None)
        self.recent_menu.add(rumps.MenuItem("Open History Folder",
                                            callback=self.on_history))

    def _make_copy_cb(self, text):
        def _cb(_):
            self.daemon.paster.copy_only(text)
        return _cb

    def on_retry(self, _):
        if not self.daemon.retry_last():
            rumps.notification("LocalFlow", "", "No failed recording to retry.")

    def on_raw_default(self, sender):
        sender.state = not sender.state
        cfg.set("raw_by_default", bool(sender.state))

    def on_sounds(self, sender):
        sender.state = not sender.state
        cfg.set("sounds", bool(sender.state))

    def on_dictionary(self, _):
        subprocess.Popen(["open", "-t", str(DICTIONARY_PATH)])

    def on_history(self, _):
        HISTORY_DIR.mkdir(exist_ok=True)
        subprocess.Popen(["open", str(HISTORY_DIR)])

    def on_perms(self, _):
        from onboarding import show_permissions_window
        show_permissions_window(self.daemon)

    def on_login(self, sender):
        sender.state = not sender.state
        try:
            if sender.state:
                subprocess.run(["launchctl", "load", "-w",
                                f"{PLIST_PATH}"], check=False)
            else:
                subprocess.run(["launchctl", "unload", "-w",
                                f"{PLIST_PATH}"], check=False)
        except Exception:
            log.exception("login toggle")

    def on_about(self, _):
        avg = pipeline.average_latency()
        avg_s = f"{avg/1000:.1f}s" if avg else "n/a"
        rumps.alert(
            title="LocalFlow",
            message=(f"Fully local voice dictation.\n\n"
                     f"ASR: faster-whisper {cfg.model} (CPU)\n"
                     f"Cleanup: {'Ollama ' + cfg.ollama_model if cfg.llm_enabled else 'quick clean (regex)'}\n"
                     f"Avg stop→paste (last 20): {avg_s}\n\n"
                     f"Nothing leaves this Mac. Ever."),
            ok="Nice",
        )

    def on_setup(self, _):
        from onboarding import show_onboarding
        show_onboarding(self.daemon)

    def on_quit(self, _):
        if self.daemon.state == "recording":
            self.daemon.cancel()
            time.sleep(0.5)
        rumps.quit_application()
