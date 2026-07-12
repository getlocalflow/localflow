"""Text pipeline: dictionary replacements, filler stripping, tone presets,
optional Ollama cleanup (Phase 2), history writes, timing log."""
import datetime
import json
import logging
import re
import shutil
import time
from pathlib import Path

import requests

from config import cfg, HISTORY_DIR, REPLACEMENTS_PATH, APP_MODES_PATH, TIMINGS_LOG, LOG_DIR

log = logging.getLogger("localflow.pipeline")

# --- Filler stripping (Phase 1, regex; the LLM does better in Phase 2) -------

FILLER_RE = re.compile(
    r"(?:,\s*)?\b(um+|uh+|uhm+|erm+|hmm+)\b[,.]?\s*", re.IGNORECASE
)
DOUBLE_SPACE_RE = re.compile(r"  +")
SPACE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")


def strip_fillers(text: str) -> str:
    text = FILLER_RE.sub(" ", text)
    text = SPACE_PUNCT_RE.sub(r"\1", text)
    text = DOUBLE_SPACE_RE.sub(" ", text)
    return text.strip()


# --- Dictionary replacements --------------------------------------------------

def load_replacements() -> dict:
    try:
        data = json.loads(REPLACEMENTS_PATH.read_text())
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


def apply_replacements(text: str) -> str:
    # longest keys first so "tresor royale" wins over "tresor royal";
    # word boundaries so partial words never get mangled
    for wrong, right in sorted(load_replacements().items(),
                               key=lambda kv: -len(kv[0])):
        text = re.sub(r"\b" + re.escape(wrong) + r"\b", right, text,
                      flags=re.IGNORECASE)
    return text


# --- Per-app tone presets (Phase 2 uses these with the LLM) -------------------

DEFAULT_APP_MODES = {
    "raw": ["com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp",
            "com.microsoft.VSCode", "com.apple.dt.Xcode", "com.todesktop.230313mzl4w4u92"],
    "formal": ["com.apple.mail", "com.microsoft.Outlook"],
    "casual": ["com.tinyspeck.slackmacgap", "com.apple.MobileSMS", "com.hnc.Discord"],
}


def load_app_modes() -> dict:
    try:
        return json.loads(APP_MODES_PATH.read_text())
    except Exception:
        return DEFAULT_APP_MODES


def tone_for_bundle(bundle_id: str | None) -> str:
    if not bundle_id:
        return "clean"
    modes = load_app_modes()
    for tone, bundles in modes.items():
        if bundle_id in bundles:
            return tone
    return "clean"


# --- Ollama cleanup (Phase 2) --------------------------------------------------

TONE_INSTRUCTIONS = {
    "clean": "Neutral, clean prose.",
    "formal": "Polished, professional prose suitable for email.",
    "casual": "Relaxed, conversational tone suitable for chat.",
}

FORMATTER_PROMPT = """You are a dictation formatter, NOT an assistant. Rewrite the raw
transcript into exactly what the speaker would have typed.
RULES:
- Remove filler words (um, uh, like, you know) and false starts.
- Apply self-corrections: when the speaker corrects themselves, keep ONLY the
  final corrected version and drop what they said before the correction.
- Fix punctuation, capitalization, and paragraph breaks. Format spoken lists as lists.
- {tone}
- Never use em dashes or en dashes. Use commas, periods, or parentheses.
- NEVER change the meaning, add content, answer questions, or invent words.
- Preserve these terms verbatim if present: {vocab}
Return ONLY the formatted text, nothing else.

EXAMPLES:
Input: um so the budget is fifty thousand actually make that seventy five thousand
Output: The budget is seventy five thousand.

Input: send me the file from yesterday no wait the one from monday
Output: Send me the file from Monday.

Input: first update the sop second email the team and third schedule the demo
Output: 1. Update the SOP
2. Email the team
3. Schedule the demo

Input: hey did you uh did you get a chance to look at the thing I sent
Output: Hey, did you get a chance to look at the thing I sent?"""


def llm_cleanup(text: str, tone: str, vocab: str) -> str | None:
    """Ollama formatter pass. Returns None on any failure (caller falls back)."""
    if not cfg.llm_enabled or len(text) < cfg.llm_min_chars or tone == "raw":
        return None
    try:
        r = requests.post(
            f"{cfg.ollama_url}/api/generate",
            json={
                "model": cfg.ollama_model,
                "system": FORMATTER_PROMPT.format(
                    tone=TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["clean"]),
                    vocab=vocab or "(none)",
                ),
                "prompt": text,
                "stream": False,
                "keep_alive": -1,
                "options": {"temperature": 0},
            },
            timeout=cfg.ollama_timeout_s,
        )
        r.raise_for_status()
        out = r.json().get("response", "").strip()
        # Guard against over-rewriting: reject if the LLM went wild on length.
        # Lower bound is generous because self-corrections legitimately shrink
        # text ("X no wait Y" collapses to just "Y").
        if out and 0.28 <= len(out) / max(len(text), 1) <= 1.6:
            return out
        return None
    except Exception:
        return None


def ollama_ping():
    """Keep the formatter model warm; silent on failure."""
    if not cfg.llm_enabled:
        return
    try:
        requests.post(
            f"{cfg.ollama_url}/api/generate",
            json={"model": cfg.ollama_model, "prompt": "", "stream": False,
                  "keep_alive": -1, "options": {"num_predict": 1}},
            timeout=2,
        )
    except Exception:
        pass


# --- The full text pass ---------------------------------------------------------

def process(raw_text: str, tone: str, vocab: str) -> tuple[str, str]:
    """raw ASR text -> (final_text, cleanup_kind: 'llm'|'quick'|'raw')."""
    text = apply_replacements(raw_text)
    if tone == "raw":
        return text, "raw"
    cleaned = llm_cleanup(text, tone, vocab)
    if cleaned is not None:
        return apply_replacements(cleaned), "llm"
    return strip_fillers(text), "quick"


# --- History ("never lose words") ----------------------------------------------

def new_history_entry() -> Path:
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    d = HISTORY_DIR / stamp
    n = 1
    while d.exists():
        n += 1
        d = HISTORY_DIR / f"{stamp}-{n}"
    d.mkdir(parents=True)
    return d


def write_history_text(entry: Path, raw: str | None, clean: str | None, meta: dict):
    if raw is not None:
        (entry / "raw.txt").write_text(raw)
    if clean is not None:
        (entry / "clean.txt").write_text(clean)
    (entry / "meta.json").write_text(json.dumps(meta, indent=1))


def recent_history(n: int = 5) -> list[dict]:
    out = []
    if not HISTORY_DIR.exists():
        return out
    for d in sorted(HISTORY_DIR.iterdir(), reverse=True):
        clean, raw = d / "clean.txt", d / "raw.txt"
        text_file = clean if clean.exists() else raw
        if not text_file.exists():
            continue
        out.append({
            "dir": d,
            "text": text_file.read_text().strip(),
            "raw": raw.read_text().strip() if raw.exists() else "",
            "mtime": text_file.stat().st_mtime,
        })
        if len(out) >= n:
            break
    return out


def prune_history():
    if not HISTORY_DIR.exists():
        return
    entries = sorted(HISTORY_DIR.iterdir())
    cutoff = time.time() - cfg.history_max_days * 86400
    for i, d in enumerate(entries):
        too_old = d.stat().st_mtime < cutoff
        overflow = len(entries) - i > cfg.history_max_entries
        if too_old or overflow:
            wav = d / "audio.wav"
            if wav.exists():
                wav.unlink()  # prune WAVs first; transcripts are tiny
        if len(entries) - i > 1000:
            shutil.rmtree(d, ignore_errors=True)


# --- Timing log -----------------------------------------------------------------

def log_timing(record: dict):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with TIMINGS_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")


def average_latency(n: int = 20) -> float | None:
    try:
        lines = TIMINGS_LOG.read_text().splitlines()[-n:]
        vals = [json.loads(l).get("t_stop_to_paste") for l in lines]
        vals = [v for v in vals if v]
        return sum(vals) / len(vals) if vals else None
    except Exception:
        return None
