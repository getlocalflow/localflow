"""Audio capture + background chunked transcription.

Capture starts on the F13 down-edge (first-word protection). A background ASR
worker transcribes completed chunks (cut at silence boundaries) while the user
is still speaking, so on stop only the tail remains.
"""
import json
import logging
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from config import cfg, DICTIONARY_PATH

log = logging.getLogger("localflow.audio")

TARGET_SR = 16000


def pick_input_device():
    """Choose input by cfg.device_priority (substring match), else default.

    Returns (device_index, samplerate) or (None, None) if no input exists.
    """
    devices = sd.query_devices()
    inputs = [(i, d) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    if not inputs:
        return None, None
    for pref in cfg.device_priority:
        for i, d in inputs:
            if pref.lower() in d["name"].lower():
                return i, int(d["default_samplerate"])
    try:
        i = sd.default.device[0]
        if i is not None and i >= 0 and devices[i]["max_input_channels"] > 0:
            return i, int(devices[i]["default_samplerate"])
    except Exception:
        pass
    i, d = inputs[0]
    return i, int(d["default_samplerate"])


def resample_to_16k(audio: np.ndarray, sr: int) -> np.ndarray:
    """Linear-interp resample float32 mono to 16 kHz (fine for ASR)."""
    if sr == TARGET_SR:
        return audio
    n_out = int(len(audio) * TARGET_SR / sr)
    x_in = np.linspace(0.0, 1.0, len(audio), endpoint=False)
    x_out = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_out, x_in, audio).astype(np.float32)


class Recorder:
    """Mic capture with a live RMS level and silence tracking."""

    def __init__(self):
        self.stream = None
        self.sr = TARGET_SR
        self.buf = []                # list of float32 arrays @ device sr
        self.lock = threading.Lock()
        self.level = 0.0             # smoothed RMS for the HUD waveform
        self.started_at = None
        self.device_name = None

    def start(self) -> bool:
        dev, native_sr = pick_input_device()
        if dev is None:
            return False
        self.device_name = sd.query_devices(dev)["name"]
        self.buf = []
        self.started_at = time.monotonic()
        # Ask for 16k; CoreAudio usually converts. Fall back to native.
        for sr in (TARGET_SR, native_sr):
            try:
                self.stream = sd.InputStream(
                    device=dev, channels=1, samplerate=sr, dtype="float32",
                    blocksize=int(sr * 0.03), callback=self._callback,
                )
                self.stream.start()
                self.sr = sr
                log.info("recording: device=%r sr=%d", self.device_name, sr)
                return True
            except Exception as e:
                log.warning("stream open failed at %d Hz: %s", sr, e)
        return False

    def _callback(self, indata, frames, t, status):
        mono = indata[:, 0].copy()
        with self.lock:
            self.buf.append(mono)
        rms = float(np.sqrt(np.mean(mono ** 2)))
        self.level = 0.6 * self.level + 0.4 * rms

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self.started_at else 0.0

    def snapshot(self) -> np.ndarray:
        """All audio so far (device sr, float32 mono)."""
        with self.lock:
            return np.concatenate(self.buf) if self.buf else np.zeros(0, np.float32)

    def stop(self) -> np.ndarray:
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        return self.snapshot()


def find_silence_cut(audio: np.ndarray, sr: int, min_silence_s: float, floor: float) -> int | None:
    """Index of the end of the last silence run of >= min_silence_s, or None."""
    win = int(sr * 0.03)
    if len(audio) < win * 4:
        return None
    n_win = len(audio) // win
    rms = np.sqrt(np.mean(audio[: n_win * win].reshape(n_win, win) ** 2, axis=1))
    need = max(1, int(min_silence_s / 0.03))
    silent = rms < floor
    run = 0
    best = None
    for i, s in enumerate(silent):
        run = run + 1 if s else 0
        if run >= need:
            best = (i + 1) * win
    return best


def trim_silence(audio: np.ndarray, sr: int, floor: float) -> np.ndarray:
    """Trim leading/trailing silence (keep 150ms pad)."""
    win = int(sr * 0.03)
    if len(audio) < win * 2:
        return audio
    n_win = len(audio) // win
    rms = np.sqrt(np.mean(audio[: n_win * win].reshape(n_win, win) ** 2, axis=1))
    loud = np.nonzero(rms >= floor)[0]
    if len(loud) == 0:
        return np.zeros(0, np.float32)
    pad = int(0.15 * sr)
    start = max(0, loud[0] * win - pad)
    end = min(len(audio), (loud[-1] + 1) * win + pad)
    return audio[start:end]


def is_silent(audio: np.ndarray, floor: float) -> bool:
    if len(audio) == 0:
        return True
    return float(np.sqrt(np.mean(audio ** 2))) < floor


def load_dictionary_prompt() -> str:
    """Build the Whisper initial_prompt from dictionary.txt (rare terms last)."""
    try:
        terms = [
            l.strip() for l in DICTIONARY_PATH.read_text().splitlines()
            if l.strip() and not l.startswith("#")
        ]
    except FileNotFoundError:
        terms = []
    if not terms:
        return ""
    return "Vocabulary: " + ", ".join(terms) + "."


class ASRWorker:
    """Serialized faster-whisper worker on a background thread, kept warm."""

    def __init__(self):
        self.model = None
        self.q = queue.Queue()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="asr")
        self.thread.start()

    def _load(self):
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # fully local: never phone home
        from faster_whisper import WhisperModel
        t0 = time.monotonic()
        try:
            self.model = WhisperModel(
                cfg.model, device="cpu", compute_type=cfg.compute_type,
                cpu_threads=cfg.cpu_threads, local_files_only=True,
            )
        except Exception:
            # model not cached yet: allow the one-time download
            os.environ.pop("HF_HUB_OFFLINE", None)
            self.model = WhisperModel(
                cfg.model, device="cpu", compute_type=cfg.compute_type,
                cpu_threads=cfg.cpu_threads,
            )
        # Page the weights in with a silent clip so the first real call is warm.
        self.model.transcribe(np.zeros(TARGET_SR // 2, np.float32), language="en")
        log.info("ASR model %s warm in %.1fs", cfg.model, time.monotonic() - t0)
        self.ready.set()

    def _run(self):
        try:
            self._load()
        except Exception:
            log.exception("ASR model load failed")
            return
        while True:
            job = self.q.get()
            if job is None:
                return
            audio, prompt, out, done = job
            try:
                segments, _info = self.model.transcribe(
                    audio, language="en", beam_size=cfg.beam_size,
                    initial_prompt=prompt or None,
                    condition_on_previous_text=False,
                    vad_filter=True,
                )
                out.append(" ".join(s.text.strip() for s in segments).strip())
            except Exception:
                log.exception("transcribe failed")
                out.append(None)  # sentinel: this chunk failed
            finally:
                done.set()

    def transcribe_async(self, audio_16k: np.ndarray, prompt: str):
        """Queue a chunk; returns (result_list, done_event)."""
        out, done = [], threading.Event()
        self.q.put((audio_16k, prompt, out, done))
        return out, done


class ChunkedSession:
    """One dictation: recorder + incremental chunk transcription."""

    def __init__(self, asr: ASRWorker):
        self.asr = asr
        self.rec = Recorder()
        self.flushed = 0             # samples (device sr) already sent to ASR
        self.pending = []            # [(out, done)] in order
        self.base_prompt = load_dictionary_prompt()

    def start(self) -> bool:
        return self.rec.start()

    def tick(self):
        """Called periodically while recording: flush a chunk if we can."""
        audio = self.rec.snapshot()
        unflushed = audio[self.flushed:]
        if len(unflushed) / self.rec.sr < cfg.chunk_seconds:
            return
        cut = find_silence_cut(
            unflushed, self.rec.sr, cfg.chunk_min_silence_s, cfg.silence_rms
        )
        if cut is None or cut < self.rec.sr:  # need >=1s to be worth it
            return
        chunk = unflushed[:cut]
        self.flushed += cut
        if is_silent(chunk, cfg.silence_rms):
            return
        chunk16 = resample_to_16k(chunk, self.rec.sr)
        self.pending.append(self.asr.transcribe_async(chunk16, self._prompt()))
        log.info("flushed chunk: %.1fs (total flushed %.1fs)",
                 len(chunk) / self.rec.sr, self.flushed / self.rec.sr)

    def _prompt(self) -> str:
        """Dictionary + tail of prior text for context continuity."""
        prior = " ".join(r[0] or "" for r in (p[0] for p in self.pending) if r)
        tail = prior[-200:] if prior else ""
        return (self.base_prompt + " " + tail).strip()[:800]

    def stop_capture(self) -> np.ndarray:
        """Stop the mic and return all captured audio (device sr). Call this
        first so the WAV can hit disk BEFORE transcription is attempted."""
        return self.rec.stop()

    def finish(self, timeout: float, audio: np.ndarray | None = None) -> tuple[str | None, np.ndarray]:
        """Transcribe the tail, join all chunks.

        Returns (text or None-on-failure, full_audio_device_sr).
        """
        if audio is None:
            audio = self.rec.stop()
        tail = audio[self.flushed:]
        tail = trim_silence(tail, self.rec.sr, cfg.silence_rms)
        if len(tail) > 0 and not is_silent(tail, cfg.silence_rms):
            tail16 = resample_to_16k(tail, self.rec.sr)
            self.pending.append(self.asr.transcribe_async(tail16, self._prompt()))
        parts = []
        deadline = time.monotonic() + timeout
        for out, done in self.pending:
            if not done.wait(max(0.1, deadline - time.monotonic())):
                log.error("ASR watchdog tripped")
                return None, audio
            if out and out[0] is None:
                return None, audio
            if out and out[0]:
                parts.append(out[0])
        return " ".join(parts).strip(), audio


def save_wav(path: Path, audio: np.ndarray, sr: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
