"""First-run: data dir + samples, and the one-time model download window."""
import logging
import os
import shutil
import threading
import time

from core.config import cfg, DATA_DIR, ROOT, DICTIONARY_PATH, REPLACEMENTS_PATH

log = logging.getLogger("localflow.onboarding")


def first_run_setup():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for sample, live in ((ROOT / "dictionary.sample.txt", DICTIONARY_PATH),
                         (ROOT / "replacements.sample.json", REPLACEMENTS_PATH)):
        if not live.exists() and sample.exists():
            shutil.copy(sample, live)


def model_cached() -> bool:
    had_offline = "HF_HUB_OFFLINE" in os.environ
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from faster_whisper.utils import download_model
        download_model(cfg.model, local_files_only=True)
        return True
    except Exception:
        return False
    finally:
        if not had_offline:
            os.environ.pop("HF_HUB_OFFLINE", None)


def ensure_model(root) -> bool:
    if model_cached():
        return True
    import tkinter as tk
    from tkinter import ttk
    win = tk.Toplevel(root)
    win.title("LocalFlow - one-time setup")
    win.geometry("420x140")
    win.attributes("-topmost", True)
    tk.Label(win, text="Downloading the speech model (about 1.6 GB).\n"
                       "This happens once. On typical Wi-Fi it takes about\n"
                       "10 minutes. LocalFlow starts when it finishes.",
             justify="left", padx=16, pady=10).pack(anchor="w")
    bar = ttk.Progressbar(win, mode="indeterminate", length=380)
    bar.pack(pady=6)
    bar.start(12)
    result = {}

    def work():
        try:
            from faster_whisper import download_model
            download_model(cfg.model)
            result["ok"] = True
        except Exception as e:
            log.exception("model download")
            result["ok"] = False
            result["err"] = str(e)[:120]

    t = threading.Thread(target=work, daemon=True)
    t.start()
    while t.is_alive():
        root.update()          # keeps the progress window responsive
        time.sleep(0.05)
    bar.stop()
    win.destroy()
    if not result.get("ok"):
        from tkinter import messagebox
        messagebox.showerror(
            "LocalFlow",
            "The speech model download failed: " + result.get("err", "") +
            "\n\nThis is usually a network hiccup. Check your internet "
            "connection, then start LocalFlow again from the Start menu; "
            "the download resumes where it left off.")
        return False
    return True
