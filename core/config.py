"""LocalFlow configuration: defaults + config.toml overlay (hot-reloadable)."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
HISTORY_DIR = ROOT / "history"
SOUNDS_DIR = ROOT / "sounds"
DICTIONARY_PATH = ROOT / "dictionary.txt"
REPLACEMENTS_PATH = ROOT / "replacements.json"
APP_MODES_PATH = ROOT / "app_modes.json"
LOG_DIR = Path.home() / "Library" / "Logs" / "LocalFlow"
TIMINGS_LOG = LOG_DIR / "timings.log"
DAEMON_LOG = LOG_DIR / "localflow.log"

DEFAULTS = {
    # audio
    "device_priority": [],        # substring match, in order; empty = system default mic
    "samplerate": 16000,          # requested; falls back to device native + resample
    "silence_rms": 0.010,         # below this = silence (float32 RMS)
    "mic_warn_after_s": 3.0,      # in-pill "not hearing anything" warning
    "chunk_seconds": 8.0,         # background-transcribe when this much unflushed audio
    "chunk_min_silence_s": 0.3,   # cut chunks at silence >= this
    "max_recording_s": 300.0,     # auto-stop cap (5 min)
    "warn_recording_s": 270.0,    # amber timer + tick at 4:30
    # asr
    "model": "large-v3-turbo",
    "compute_type": "int8",
    "cpu_threads": 8,
    "beam_size": 1,               # greedy: 3-5x faster than beam 5 on CPU
    "asr_watchdog_s": 20.0,
    # trigger: mouse button mapped in Logi Options+ to a key combo.
    # Default ⌃⌥⌘D (vk 2 = the D key). Raw mode = hold Shift while pressing.
    "trigger_vk": 2,
    "trigger_mods": ["ctrl", "alt", "cmd"],
    # behavior
    "debounce_ms": 250,
    "too_short_ms": 700,
    "hold_cancel_ms": 600,
    "spinner_gate_ms": 300,
    "sounds": True,
    "raw_by_default": False,
    "llm_min_chars": 60,          # short utterances skip the LLM (Phase 2)
    # cleanup (Phase 2)
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen2.5:3b",
    "ollama_timeout_s": 3.0,
    "llm_enabled": False,         # flipped on in Phase 2 setup
    # history
    "history_max_entries": 200,
    "history_max_days": 30,
}


class Config:
    def __init__(self):
        self._data = dict(DEFAULTS)
        self._mtime = None
        self.reload()

    def reload(self):
        """Overlay config.toml if present and changed. Returns True if reloaded."""
        if not CONFIG_PATH.exists():
            return False
        mtime = CONFIG_PATH.stat().st_mtime
        if mtime == self._mtime:
            return False
        self._mtime = mtime
        try:
            with CONFIG_PATH.open("rb") as f:
                overlay = tomllib.load(f)
            self._data = {**DEFAULTS, **overlay}
            return True
        except Exception:
            return False  # bad toml: keep last-good config

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name)

    def set(self, name, value):
        """Set a value and persist the full config to config.toml."""
        self._data[name] = value
        lines = ["# LocalFlow config — mirrors the menu; hot-reloaded.\n"]
        for k, v in self._data.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            elif isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            elif isinstance(v, list):
                items = ", ".join(f'"{x}"' for x in v)
                lines.append(f"{k} = [{items}]")
        CONFIG_PATH.write_text("\n".join(lines) + "\n")
        self._mtime = CONFIG_PATH.stat().st_mtime


cfg = Config()
