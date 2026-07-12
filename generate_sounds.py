#!/usr/bin/env python3
"""Generate LocalFlow's five sound cues as WAV files.

One family: short, soft-attack, marimba/glass character, all derived from the
same root so they feel like one product. Run once at build time:

    ./venv/bin/python3 generate_sounds.py
"""
import struct
import wave
from pathlib import Path

import numpy as np

SR = 48000
OUT = Path(__file__).parent / "sounds"

# Note frequencies
G4 = 392.00
B4 = 493.88
C5 = 523.25
E5 = 659.25


def marimba_note(freq: float, dur: float, gain: float = 1.0) -> np.ndarray:
    """A soft marimba-ish tone: sine + quiet 4th harmonic, fast attack, exp decay."""
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    tone = np.sin(2 * np.pi * freq * t) + 0.15 * np.sin(2 * np.pi * freq * 4 * t)
    attack = np.minimum(t / 0.005, 1.0)               # 5ms attack
    decay = np.exp(-t / (dur * 0.45))                 # exponential decay
    return (tone * attack * decay * gain).astype(np.float64)


def sequence(notes, overlap=0.02):
    """Overlap-add a list of (freq, dur, gain) notes."""
    total = sum(d for _, d, _ in notes) + 0.05
    buf = np.zeros(int(SR * total))
    pos = 0.0
    for freq, dur, gain in notes:
        n = marimba_note(freq, dur + 0.04, gain)      # small tail past nominal dur
        i = int(SR * pos)
        buf[i:i + len(n)] += n[: len(buf) - i]
        pos += dur - overlap
    return buf


def slide(f0: float, f1: float, dur: float, gain: float = 1.0) -> np.ndarray:
    """A pitch slide (for cancel)."""
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    freq = f0 + (f1 - f0) * (t / dur)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    tone = np.sin(phase)
    attack = np.minimum(t / 0.005, 1.0)
    decay = np.exp(-t / (dur * 0.5))
    return tone * attack * decay * gain


def write_wav(name: str, samples: np.ndarray, peak: float = 0.5):
    samples = samples / (np.abs(samples).max() or 1.0) * peak
    pcm = (samples * 32767).astype(np.int16)
    path = OUT / f"{name}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"  {path.name}  {len(samples)/SR*1000:.0f}ms  peak={peak}")


def main():
    OUT.mkdir(exist_ok=True)
    print("Generating LocalFlow sound cues:")
    # start: rising, expectant — G4→C5, ~120ms total
    write_wav("start", sequence([(G4, 0.06, 0.9), (C5, 0.09, 1.0)]), peak=0.42)
    # stop: neutral, closing — single damped C5, ~100ms
    write_wav("stop", marimba_note(C5, 0.10), peak=0.42)
    # done: resolved, positive — rising third C5→E5, ~200ms
    write_wav("done", sequence([(C5, 0.08, 0.9), (E5, 0.14, 1.0)]), peak=0.36)
    # error: gentle dissonance — minor second down C5→B4, ~150ms
    write_wav("error", sequence([(C5, 0.07, 1.0), (B4, 0.10, 0.9)]), peak=0.48)
    # cancel: deflating, no-blame — slide C5→G4, ~120ms
    write_wav("cancel", slide(C5, G4, 0.12), peak=0.30)
    print("done.")


if __name__ == "__main__":
    main()
