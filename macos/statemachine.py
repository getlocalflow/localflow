"""LocalFlow daemon state machine.

States: IDLE → ARMING → RECORDING → STOPPING/PROCESSING → PASTING → DONE
plus CANCELLED / ERROR / PAUSED. Coordinates the F13 listener (pynput),
recorder + chunked ASR, HUD, sounds, paste, history, and timing log.

Threading: pynput events arrive on its listener thread; ASR runs on the worker
thread; ALL AppKit (HUD) calls are marshalled to the main thread via
AppHelper.callAfter. Sounds are NSSound preloaded and fired via callAfter too
(one runloop pass ≈ 1-2ms, within budget).
"""
import logging
import threading
import time

from AppKit import NSSound
from PyObjCTools import AppHelper
import Quartz

from core import pipeline
from core.audio import ASRWorker, ChunkedSession, is_silent, save_wav, load_dictionary_prompt
from core.config import cfg, SOUNDS_DIR
from macos.paste import Paster, frontmost_app

log = logging.getLogger("localflow.state")

IDLE, RECORDING, PROCESSING, PAUSED = "idle", "recording", "processing", "paused"


class Sounds:
    def __init__(self):
        self._sounds = {}
        for name in ("start", "stop", "done", "error", "cancel"):
            path = SOUNDS_DIR / f"{name}.wav"
            if path.exists():
                s = NSSound.alloc().initWithContentsOfFile_byReference_(str(path), True)
                if s:
                    self._sounds[name] = s

    def play(self, name: str):
        if not cfg.sounds:
            return
        s = self._sounds.get(name)
        if s:
            AppHelper.callAfter(lambda: (s.stop(), s.play()))


class Daemon:
    """Owns the whole dictation lifecycle. Create on the main thread."""

    def __init__(self, hud, on_state_change=None):
        self.hud = hud
        hud.on_cancel = self.cancel
        self.on_state_change = on_state_change or (lambda s: None)
        self.sounds = Sounds()
        self.asr = ASRWorker()
        self.paster = Paster()
        self.state = IDLE
        self.session = None
        self.raw_mode = False
        self.pending_start = False
        self.last_event_t = 0.0
        self.last_failed_entry = None      # for Retry Last Recording
        self._f13_down = False
        self._f13_down_t = 0.0
        self._hold_cancel_armed = False
        self._shift_at_press = False
        self._mic_warned = False
        self._suppress_paste = False
        self._lock = threading.RLock()
        self._tap = None
        self.listener_running = False
        self.status_note = ""              # menubar status line extra

    # ---- keyboard listener (CGEventTap) -----------------------------------------
    # A listen-only event tap reads the modifier FLAGS stamped on each event —
    # which is how Logi Options+ sends its "keystroke" assignments (it does NOT
    # simulate separate modifier key presses, so pynput-style tracking misses it).

    _MOD_FLAG = {
        "ctrl": Quartz.kCGEventFlagMaskControl,
        "alt": Quartz.kCGEventFlagMaskAlternate,
        "cmd": Quartz.kCGEventFlagMaskCommand,
        "shift": Quartz.kCGEventFlagMaskShift,
    }
    _VK_F13 = 105
    _VK_ESC = 53

    def start_listener(self) -> bool:
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp))
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly, mask, self._tap_event, None)
        if not self._tap:
            log.error("CGEventTapCreate failed — Input Monitoring permission?")
            return False
        src = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), src,
                                  Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        self.listener_running = True
        return True

    def _tap_event(self, proxy, etype, event, refcon):
        try:
            if etype in (Quartz.kCGEventTapDisabledByTimeout,
                         Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self._tap, True)
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)
            if etype == Quartz.kCGEventKeyDown:
                if getattr(cfg, "debug_keys", False):
                    log.info("key down: vk=%d flags=%#x", keycode, flags)
                if self._is_trigger(keycode, flags):
                    repeat = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventAutorepeat)
                    if self._f13_down or repeat:
                        return event
                    self._f13_down = True
                    self._f13_down_t = time.monotonic()
                    self._shift_at_press = bool(
                        flags & Quartz.kCGEventFlagMaskShift)
                    self._handle_f13_down()
                elif keycode == self._VK_ESC and self.state == RECORDING:
                    self.cancel()
            elif etype == Quartz.kCGEventKeyUp:
                if self._f13_down and keycode in (cfg.trigger_vk, self._VK_F13):
                    self._f13_down = False
                    self._handle_f13_up()
        except Exception:
            log.exception("tap event")
        return event

    def _is_trigger(self, keycode: int, flags: int) -> bool:
        """Configured key with all required modifier FLAGS present (this is how
        Logi Options+ stamps its keystrokes). F13 always works too."""
        if keycode == self._VK_F13:
            return True
        if keycode != cfg.trigger_vk:
            return False
        return all(flags & self._MOD_FLAG[m] for m in cfg.trigger_mods)

    # ---- F13 edges --------------------------------------------------------------

    def _debounced(self) -> bool:
        now = time.monotonic()
        if (now - self.last_event_t) * 1000 < cfg.debounce_ms:
            return True
        self.last_event_t = now
        return False

    def _handle_f13_down(self):
        with self._lock:
            if self.state == PAUSED or self._debounced():
                return
            if self.state == IDLE:
                self._start_recording()
            elif self.state == RECORDING:
                # stop happens on RELEASE (so hold-to-cancel can work);
                # arm the hold hint timer here.
                self._hold_cancel_armed = True
                threading.Timer(cfg.hold_cancel_ms / 1000.0,
                                self._hold_hint).start()
            elif self.state == PROCESSING:
                self.pending_start = True   # chain the next dictation

    def _hold_hint(self):
        if self._f13_down and self._hold_cancel_armed and self.state == RECORDING:
            AppHelper.callAfter(self.hud.hold_cancel_hint, True)

    def _handle_f13_up(self):
        with self._lock:
            if self.state != RECORDING or not self._hold_cancel_armed:
                return
            self._hold_cancel_armed = False
            held_ms = (time.monotonic() - self._f13_down_t) * 1000
            AppHelper.callAfter(self.hud.hold_cancel_hint, False)
            if held_ms >= cfg.hold_cancel_ms:
                self.cancel()
            else:
                self._stop_recording()

    # ---- lifecycle ----------------------------------------------------------------

    def _set_state(self, s):
        self.state = s
        self.on_state_change(s)

    def _start_recording(self):
        # Shift held at press flips the default (⌥ is part of the trigger combo)
        self.raw_mode = self._shift_at_press != cfg.raw_by_default
        self.t_press = time.monotonic()
        self.sounds.play("start")                              # sound-before-work
        self.session = ChunkedSession(self.asr)
        self._mic_warned = False
        self._suppress_paste = False
        if not self.session.start():
            self._set_state(IDLE)
            self.sounds.play("error")
            AppHelper.callAfter(self.hud.show_error, "No microphone found")
            return
        self._set_state(RECORDING)
        rec = self.session.rec
        AppHelper.callAfter(self._show_recording_hud, rec)
        log.info("recording started (raw=%s)", self.raw_mode)

    def _show_recording_hud(self, rec):
        self.hud.level_source = lambda: rec.level
        self.hud.show_recording(raw=self.raw_mode)

    def tick(self):
        """Periodic (0.5s) heartbeat from the menubar timer: chunk flush,
        mic warning, auto-stop cap. Called on the main thread."""
        cfg.reload()
        if self.state != RECORDING or self.session is None:
            return
        rec = self.session.rec
        el = rec.elapsed()
        # mic-dead warning in the first seconds
        if el >= cfg.mic_warn_after_s and rec.level < cfg.silence_rms and not self._mic_warned:
            self._mic_warned = True
            self.hud.mic_warning(True)
        elif self._mic_warned and rec.level >= cfg.silence_rms:
            self._mic_warned = False
            self.hud.mic_warning(False)
        # background chunk flush (worker-thread safe: queues to ASR thread)
        threading.Thread(target=self.session.tick, daemon=True).start()
        # auto-stop cap
        if el >= cfg.max_recording_s:
            log.info("auto-stop cap reached")
            with self._lock:
                if self.state == RECORDING:
                    self._stop_recording(auto=True)

    def _stop_recording(self, auto: bool = False):
        self.t_stop = time.monotonic()
        self.sounds.play("stop")
        self._set_state(PROCESSING)
        # too-short (oops double-toggle) => non-event
        dur = self.session.rec.elapsed()
        bundle_id, app_name = frontmost_app()   # tone target sampled at STOP
        # spinner only if processing outlasts the gate
        def _maybe_spinner():
            if self.state == PROCESSING:
                self.hud.show_processing(
                    "Transcribing…" if self.raw_mode else "Polishing…")
        threading.Timer(cfg.spinner_gate_ms / 1000.0,
                        lambda: AppHelper.callAfter(_maybe_spinner)).start()
        threading.Thread(
            target=self._process, args=(dur, bundle_id, auto), daemon=True,
        ).start()

    def _process(self, dur: float, bundle_id: str | None, auto: bool):
        """Worker thread: finalize ASR, clean, then hop to main for paste."""
        session, raw_mode = self.session, self.raw_mode
        t0 = time.monotonic()
        entry = pipeline.new_history_entry()
        try:
            # WAV to disk BEFORE transcription — the never-lose-words contract
            audio = session.stop_capture()
            save_wav(entry / "audio.wav", audio, session.rec.sr)
            raw_text, _ = session.finish(timeout=cfg.asr_watchdog_s, audio=audio)

            if dur * 1000 < cfg.too_short_ms or is_silent(audio, cfg.silence_rms):
                pipeline.write_history_text(entry, raw_text or "", None,
                                            {"result": "silent"})
                AppHelper.callAfter(self._finish_silent)
                return
            if raw_text is None:
                self.last_failed_entry = entry
                pipeline.write_history_text(entry, None, None,
                                            {"result": "asr_failed"})
                AppHelper.callAfter(self._finish_error,
                                    "Transcription failed — audio saved")
                return
            if not raw_text.strip():
                pipeline.write_history_text(entry, "", None, {"result": "empty"})
                AppHelper.callAfter(self._finish_silent)
                return

            tone = "raw" if raw_mode else pipeline.tone_for_bundle(bundle_id)
            vocab = load_dictionary_prompt()
            final, kind = pipeline.process(raw_text, tone, vocab)
            meta = {"result": "ok", "tone": tone, "cleanup": kind,
                    "app": bundle_id, "auto_stop": auto,
                    "duration_s": round(dur, 1)}
            pipeline.write_history_text(entry, raw_text, final, meta)
            t_asr = time.monotonic() - t0
            AppHelper.callAfter(self._finish_paste, final, kind, t_asr, meta)
        except Exception:
            log.exception("processing failed")
            self.last_failed_entry = entry
            AppHelper.callAfter(self._finish_error,
                                "Something went wrong — audio saved")
        finally:
            pipeline.prune_history()

    # ---- finish paths (main thread) --------------------------------------------------

    def _finish_silent(self):
        self.sounds.play("cancel")
        self.hud.show_info("Nothing heard")
        self._after_cycle()

    def _finish_error(self, msg: str):
        self.sounds.play("error")
        self.hud.show_error(f"⚠ {msg}")
        self._after_cycle()

    def _finish_paste(self, text: str, kind: str, t_asr: float, meta: dict):
        if self._suppress_paste:
            self.paster.copy_only(text)
            self.hud.show_info("Saved to history, not pasted")
            self._after_cycle()
            return
        result = self.paster.paste(text)
        t_total = time.monotonic() - self.t_stop
        words = len(text.split())
        pipeline.log_timing({
            "id": meta.get("app") or "",
            "rec_dur_ms": int(meta.get("duration_s", 0) * 1000),
            "t_stop_to_paste": int(t_total * 1000),
            "t_asr_ms": int(t_asr * 1000),
            "cleanup": kind, "words": words,
        })
        if result == "secure":
            self.sounds.play("error")
            self.hud.show_error("⚠ Secure field — text copied, press ⌘V")
        else:
            self.sounds.play("done")
            note = "quick clean" if (kind == "quick" and cfg.llm_enabled) else ""
            self.hud.show_done(words, note)
        log.info("pasted %d words in %.0fms (%s)", words, t_total * 1000, kind)
        self._after_cycle()

    def _after_cycle(self):
        self._set_state(IDLE)
        self.session = None
        if self.pending_start:
            self.pending_start = False
            # brief beat so the checkmark reads, then chain the next recording
            threading.Timer(0.3, lambda: AppHelper.callAfter(
                self._chain_start)).start()

    def _chain_start(self):
        with self._lock:
            if self.state == IDLE:
                self._start_recording()

    # ---- cancel / pause ------------------------------------------------------------

    def cancel(self):
        with self._lock:
            if self.state == RECORDING:
                session = self.session
                self._set_state(IDLE)
                self.session = None
                self.sounds.play("cancel")
                AppHelper.callAfter(self.hud.show_info, "Cancelled", 0.8)
                threading.Thread(target=self._save_cancelled,
                                 args=(session,), daemon=True).start()
            elif self.state == PROCESSING:
                self._suppress_paste = True

    def _save_cancelled(self, session):
        try:
            audio = session.stop_capture()
            if len(audio) / session.rec.sr > 5.0 and not is_silent(audio, cfg.silence_rms):
                entry = pipeline.new_history_entry()
                save_wav(entry / "audio.wav", audio, session.rec.sr)
                text, _ = session.finish(timeout=cfg.asr_watchdog_s, audio=audio)
                pipeline.write_history_text(entry, text or "", None,
                                            {"result": "cancelled"})
        except Exception:
            log.exception("cancel save failed")

    def toggle_pause(self) -> bool:
        with self._lock:
            if self.state == PAUSED:
                self._set_state(IDLE)
                return False
            if self.state == RECORDING:
                self.cancel()
            self._set_state(PAUSED)
            return True

    # ---- sleep/wake -------------------------------------------------------------------

    def on_will_sleep(self):
        with self._lock:
            if self.state == RECORDING:
                log.info("sleep during recording: save, no paste")
                self._suppress_paste = True
                self._stop_recording()

    def on_did_wake(self):
        # Re-warm ASR with a silent clip; audio devices re-enumerate on next start.
        import numpy as np
        self.asr.transcribe_async(np.zeros(8000, np.float32), "")

    def retry_last(self):
        """Re-transcribe the last failed recording's WAV, copy to clipboard."""
        entry = self.last_failed_entry
        if not entry or not (entry / "audio.wav").exists():
            return False
        import wave as wv
        import numpy as np
        with wv.open(str(entry / "audio.wav"), "rb") as w:
            sr = w.getframerate()
            audio = np.frombuffer(w.readframes(w.getnframes()),
                                  np.int16).astype(np.float32) / 32767.0
        from core.audio import resample_to_16k
        out, done = self.asr.transcribe_async(
            resample_to_16k(audio, sr), load_dictionary_prompt())
        def _wait():
            if done.wait(cfg.asr_watchdog_s * 2) and out and out[0]:
                text, _ = pipeline.process(out[0], "clean", load_dictionary_prompt())
                pipeline.write_history_text(entry, out[0], text, {"result": "retried"})
                self.paster.copy_only(text)
                AppHelper.callAfter(self.hud.show_info, "Copied to clipboard", 2.0)
                self.last_failed_entry = None
        threading.Thread(target=_wait, daemon=True).start()
        return True
