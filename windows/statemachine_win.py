"""Windows daemon state machine. Mirrors macos/statemachine.py flow with
tkinter marshalling (root.after) instead of AppHelper, and injectable
collaborators so transitions are unit-testable without Win32/tk/audio.

Threading: hotkey callbacks arrive on the hook thread; ASR finalization runs
on a worker thread; ALL hud calls hop to the tk main thread via self.ui().
"""
import logging
import threading
import time

from core import pipeline
from core.audio import is_silent, save_wav, load_dictionary_prompt
from core.config import cfg, SOUNDS_DIR

log = logging.getLogger("localflow.state")

IDLE, RECORDING, PROCESSING, PAUSED = "idle", "recording", "processing", "paused"


class WinSounds:
    def __init__(self):
        self.enabled = bool(cfg.sounds)

    def play(self, name):
        if not self.enabled:
            return
        try:
            import winsound
            winsound.PlaySound(str(SOUNDS_DIR / f"{name}.wav"),
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            log.exception("sound %s", name)


class WinDaemon:
    def __init__(self, root, hud, paster, sounds=None, session_factory=None,
                 foreground=None, thread_cls=threading.Thread):
        self.root = root
        self.hud = hud
        self.paster = paster
        self.sounds = sounds
        self._session_factory = session_factory or self._real_session
        self._foreground = foreground or self._real_foreground
        self._thread_cls = thread_cls
        self.state = IDLE
        self.on_state_change = None   # main() wires this to tray.set_state
        self.paused = False
        self.raw_by_default = bool(cfg.raw_by_default)
        self.sounds_on = bool(cfg.sounds)
        self.session = None
        self.raw_mode = False
        self.status_note = ""
        self.last_text = ""
        self.last_failed_entry = None
        self._t_start = 0.0
        self._t_last_trigger = 0.0
        self._debounce_s = cfg.debounce_ms / 1000.0
        self._app_at_start = (None, None)
        self._queued = False
        hud.on_click = lambda: self.ui(self._hud_clicked)

    # ---- infrastructure ----
    def ui(self, fn, *args):
        self.root.after(0, lambda: fn(*args))

    def _set_state(self, s):
        self.state = s
        if self.on_state_change:
            try:
                self.on_state_change(s)
            except Exception:
                log.exception("state hook")

    def _real_session(self):
        from core.audio import ASRWorker, ChunkedSession
        if not hasattr(self, "_asr"):
            self._asr = ASRWorker()
        return ChunkedSession(self._asr)

    def _real_foreground(self):
        from windows.win_paste import foreground_app
        return foreground_app()

    def _play(self, name):
        if self.sounds and self.sounds_on:
            self.sounds.play(name)

    def status_text(self):
        return {IDLE: "LocalFlow - Ready", RECORDING: "Recording...",
                PROCESSING: "Processing...", PAUSED: "Paused"}[self.state] + (
                    f"  ({self.status_note})" if self.status_note else "")

    # ---- events ----
    def on_trigger(self, shift):
        now = time.monotonic()
        if now - self._t_last_trigger < self._debounce_s:
            return
        self._t_last_trigger = now
        if self.paused:
            return
        if self.state == IDLE:
            self.ui(self._start_recording, shift)
        elif self.state == RECORDING:
            self.ui(self._stop_recording, False)
        elif self.state == PROCESSING:
            self._queued = True

    def on_esc(self):
        if self.state == RECORDING:
            self.ui(self._cancel_recording)

    def _hud_clicked(self):
        if self.state == RECORDING:
            self._cancel_recording()
        else:
            self.hud.hide()

    # ---- transitions (tk main thread) ----
    def _start_recording(self, shift):
        if self.state != IDLE:
            return
        self.session = self._session_factory()
        self.raw_mode = shift != self.raw_by_default
        if not self.session.start():
            self.hud.show_error("No microphone found")
            self.status_note = "no microphone"
            return
        self._set_state(RECORDING)
        self.status_note = ""
        self._t_start = time.monotonic()
        self._app_at_start = self._foreground()
        self._play("start")
        self.hud.show_recording()
        self._tick()

    def _tick(self):
        if self.state != RECORDING:
            return
        dur = time.monotonic() - self._t_start
        try:
            self.session.tick()
            snap = self.session.rec.snapshot() if hasattr(self.session.rec, "snapshot") else None
            if snap is not None and len(snap):
                import numpy as np
                tail = snap[-int(self.session.rec.sr * 0.15):]
                self.hud.set_level(float(np.sqrt((tail ** 2).mean())))
        except Exception:
            log.exception("tick")
        self.hud.set_timer(dur, warn=dur >= cfg.warn_recording_s)
        if dur >= cfg.max_recording_s:
            self._stop_recording(auto=True)
            return
        self.root.after(120, self._tick)

    def _stop_recording(self, auto):
        if self.state != RECORDING:
            return
        dur = time.monotonic() - self._t_start
        self._set_state(PROCESSING)
        self._play("stop")
        self.hud.show_processing()
        app_key = self._app_at_start[0]
        self._thread_cls(target=self._process, args=(dur, app_key, auto),
                         daemon=True, name="process").start()

    def _cancel_recording(self):
        if self.state != RECORDING:
            return
        try:
            self.session.stop_capture()
        except Exception:
            log.exception("cancel stop")
        self._set_state(IDLE)
        self._play("cancel")
        self.hud.hide()

    # ---- worker thread ----
    def _process(self, dur, app_key, auto):
        session, raw_mode = self.session, self.raw_mode
        t0 = time.monotonic()
        entry = pipeline.new_history_entry()
        try:
            audio = session.stop_capture()
            save_wav(entry / "audio.wav", audio, session.rec.sr)
            raw_text, _ = session.finish(timeout=cfg.asr_watchdog_s, audio=audio)
            if dur * 1000 < cfg.too_short_ms or is_silent(audio, cfg.silence_rms):
                pipeline.write_history_text(entry, raw_text or "", None,
                                            {"result": "silent"})
                self.ui(self._finish_silent)
                return
            if raw_text is None:
                self.last_failed_entry = entry
                pipeline.write_history_text(entry, None, None,
                                            {"result": "asr_failed"})
                self.ui(self._finish_error, "Transcription failed - audio saved")
                return
            if not raw_text.strip():
                pipeline.write_history_text(entry, "", None, {"result": "empty"})
                self.ui(self._finish_silent)
                return
            tone = "raw" if raw_mode else pipeline.tone_for_bundle(app_key)
            vocab = load_dictionary_prompt()
            final, kind = pipeline.process(raw_text, tone, vocab)
            meta = {"result": "ok", "tone": tone, "cleanup": kind,
                    "app": app_key, "auto_stop": auto,
                    "duration_s": round(dur, 1)}
            pipeline.write_history_text(entry, raw_text, final, meta)
            t_asr = time.monotonic() - t0
            self.ui(self._finish_paste, final, kind, t_asr, meta)
        except Exception:
            log.exception("processing failed")
            self.last_failed_entry = entry
            self.ui(self._finish_error, "Something went wrong - audio saved")
        finally:
            pipeline.prune_history()

    # ---- finish paths (tk main thread) ----
    def _finish_silent(self):
        self._set_state(IDLE)
        self.hud.hide()
        self._maybe_dequeue()

    def _finish_error(self, msg):
        self._set_state(IDLE)
        self._play("error")
        self.hud.show_error(msg)
        self.status_note = msg
        self._maybe_dequeue()

    def _finish_paste(self, text, kind, t_asr, meta):
        self.last_text = text
        ok = self.paster.paste(text)
        self._set_state(IDLE)
        self._play("done" if ok else "error")
        words = len(text.split())
        if ok:
            self.hud.show_done(words)
        else:
            self.hud.show_error("Paste failed - use Copy Last Transcript")
        pipeline.log_timing({"t_total_ms": round(t_asr * 1000), "words": words,
                             **meta})
        log.info("pasted %d words in %dms (%s)", words, t_asr * 1000, kind)
        self._maybe_dequeue()

    def _maybe_dequeue(self):
        if self._queued:
            self._queued = False
            self.ui(self._start_recording, False)

    # ---- menu actions (tk main thread via ui()) ----
    def toggle_pause(self):
        self.paused = not self.paused
        self._set_state(PAUSED if self.paused else IDLE)

    def copy_last(self):
        if self.last_text:
            self.paster.copy_only(self.last_text)

    def retry_last(self):
        entry = self.last_failed_entry
        if not entry:
            return
        wav = entry / "audio.wav"
        if not wav.exists():
            return
        def rerun():
            import wave as wv
            import numpy as np
            with wv.open(str(wav)) as w:
                sr = w.getframerate()
                audio = np.frombuffer(w.readframes(w.getnframes()),
                                      np.int16).astype(np.float32) / 32767
            session = self._session_factory()
            session.flushed = len(audio)
            from core.audio import resample_to_16k
            a16 = resample_to_16k(audio, sr)
            out, done = session.asr.transcribe_async(a16, session.base_prompt)
            if done.wait(cfg.asr_watchdog_s) and out and out[0]:
                final, kind = pipeline.process(out[0], "default",
                                               load_dictionary_prompt())
                self.ui(self._finish_paste, final, kind, 0.0,
                        {"result": "retry_ok"})
            else:
                self.ui(self._finish_error, "Retry failed - audio still saved")
        self._set_state(PROCESSING)
        self.hud.show_processing()
        self._thread_cls(target=rerun, daemon=True, name="retry").start()

    def toggle_raw_default(self):
        self.raw_by_default = not self.raw_by_default
        cfg.set("raw_by_default", self.raw_by_default)

    def toggle_sounds(self):
        self.sounds_on = not self.sounds_on
        cfg.set("sounds", self.sounds_on)

    def open_dictionary(self):
        import os
        from core.config import DICTIONARY_PATH
        os.startfile(str(DICTIONARY_PATH))  # noqa: windows only

    def open_replacements(self):
        import os
        from core.config import REPLACEMENTS_PATH
        os.startfile(str(REPLACEMENTS_PATH))  # noqa: windows only

    def open_history(self):
        import os
        from core.config import HISTORY_DIR
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(HISTORY_DIR))  # noqa: windows only
