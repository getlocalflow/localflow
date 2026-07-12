#!/usr/bin/env python3
"""Visual smoke-test for the HUD pill: cycles recording → processing → done,
screenshotting each state. Run: ./venv/bin/python3 test_hud.py"""
import math
import subprocess
import time

from AppKit import NSApplication
from Foundation import NSTimer
from PyObjCTools import AppHelper

import hud as hud_mod

app = NSApplication.sharedApplication()
h = hud_mod.HUD()

t0 = time.time()
h.level_source = lambda: 0.03 + 0.025 * math.sin((time.time() - t0) * 6.0)

SHOTS = "/tmp/localflow-hud"
subprocess.run(["mkdir", "-p", SHOTS])


def shot(name):
    subprocess.run(["screencapture", "-x", f"{SHOTS}/{name}.png"])


def step2(_):
    shot("1-recording")
    h.show_processing("Polishing…")
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0, False, step3)


def step3(_):
    shot("2-processing")
    h.show_done(47)
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.8, False, step4)


def step4(_):
    shot("3-done")
    h.show_error("⚠ Secure field — text copied, press ⌘V")
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.8, False, step5)


def step5(_):
    shot("4-error")
    h.hide()
    AppHelper.stopEventLoop()


def start(_):
    h.show_recording(raw=False)
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.5, False, step2)


NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.3, False, start)
print("running HUD test…")
AppHelper.runEventLoop()
print(f"done — screenshots in {SHOTS}/")
