"""Cross-platform pipeline logic (pure python)."""
from core import pipeline


def test_strip_fillers():
    assert pipeline.strip_fillers("um so, uh, hello there hmm") == "so hello there"


def test_tone_for_unknown_app_is_default():
    assert pipeline.tone_for_bundle("nonexistent.app.id.12345") in (
        "clean", "raw", "formal", "casual")


def test_smartish_replacements_roundtrip(tmp_path, monkeypatch):
    rp = tmp_path / "replacements.json"
    rp.write_text('{"cooper netties": "Kubernetes"}')
    monkeypatch.setattr(pipeline, "REPLACEMENTS_PATH", rp)
    assert "Kubernetes" in pipeline.apply_replacements("i love cooper netties a lot")
