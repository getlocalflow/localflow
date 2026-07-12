"""State transitions with everything faked. No tkinter, no Win32, no audio."""
import numpy as np
import pytest

from windows.statemachine_win import WinDaemon


class FakeRoot:
    """Runs only zero-delay callbacks (the ui() marshal). Delayed jobs like
    the 120ms tick reschedule are dropped, otherwise tests would loop."""

    def after(self, ms, fn=None, *a):
        if fn and ms == 0:
            fn(*a)
        return "job"

    def after_cancel(self, job):
        pass


class InlineThread:
    """threading.Thread stand-in that runs target() synchronously on start()."""

    def __init__(self, target=None, args=(), daemon=None, name=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


class FakeHUD:
    def __init__(self):
        self.calls = []
        self.on_click = None

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append(name)
        return rec


class FakePaster:
    def __init__(self):
        self.pasted = []

    def paste(self, text):
        self.pasted.append(text)
        return True

    def copy_only(self, text):
        return True


class FakeSession:
    def __init__(self, text="hello world one two", fail=False):
        self.text, self.fail = text, fail
        self.rec = type("R", (), {"sr": 16000})()

    def start(self):
        return True

    def tick(self):
        pass

    def stop_capture(self):
        return np.random.default_rng(1).normal(0, 0.2, 32000).astype(np.float32)

    def finish(self, timeout, audio=None):
        return (None if self.fail else self.text), audio


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    from core import config, pipeline
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(pipeline, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(pipeline, "TIMINGS_LOG", tmp_path / "timings.log")
    monkeypatch.setattr(pipeline, "LOG_DIR", tmp_path / "logs")
    hud, paster = FakeHUD(), FakePaster()
    d = WinDaemon(FakeRoot(), hud, paster, sounds=None,
                  session_factory=lambda: FakeSession(),
                  foreground=lambda: ("notepad.exe", "Untitled"),
                  thread_cls=InlineThread)
    d._debounce_s = 0            # tests fire triggers back-to-back
    return d, hud, paster


def test_trigger_starts_and_stops_with_paste(daemon):
    d, hud, paster = daemon
    states = []
    d.on_state_change = states.append
    d.on_trigger(shift=False)
    assert d.state == "recording"
    d._t_start -= 2.0            # pretend 2s elapsed (bypasses too_short)
    d.on_trigger(shift=False)
    assert paster.pasted and "hello world" in paster.pasted[0]
    assert d.state == "idle"
    assert "show_done" in hud.calls
    assert states == ["recording", "processing", "idle"]


def test_esc_cancels_recording_without_paste(daemon):
    d, hud, paster = daemon
    d.on_trigger(shift=False)
    d.on_esc()
    assert d.state == "idle"
    assert not paster.pasted


def test_asr_failure_shows_error_and_keeps_audio(daemon, tmp_path):
    d, hud, paster = daemon
    d._session_factory = lambda: FakeSession(fail=True)
    d.on_trigger(shift=False)
    d._t_start -= 2.0
    d.on_trigger(shift=False)
    assert not paster.pasted
    assert "show_error" in hud.calls
    wavs = list((tmp_path / "history").rglob("audio.wav"))
    assert wavs, "audio must be saved before transcription is attempted"


def test_paused_ignores_trigger(daemon):
    d, hud, paster = daemon
    d.toggle_pause()
    d.on_trigger(shift=False)
    assert d.state == "paused"


def test_queued_trigger_starts_after_processing(daemon):
    d, hud, paster = daemon
    d._set_state("processing")
    d.on_trigger(shift=False)
    assert d._queued
    d._maybe_dequeue()
    assert d.state == "recording"


def test_pause_blocks_queued_dictation(daemon):
    d, hud, paster = daemon
    d._set_state("processing")
    d.on_trigger(shift=False)
    d.toggle_pause()
    d._maybe_dequeue()
    assert d.state == "paused"
    assert not d.paused or d.state != "recording"


def test_sounds_toggle_uses_live_config(daemon, monkeypatch):
    d, hud, paster = daemon
    from core.config import cfg
    calls = []
    monkeypatch.setattr(cfg, "set", lambda k, v: calls.append((k, v)))
    before = d.sounds_on
    d.toggle_sounds()
    assert calls and calls[0][0] == "sounds" and calls[0][1] == (not before)
