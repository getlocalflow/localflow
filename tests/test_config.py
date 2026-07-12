"""Platform path resolution. Runs on both mac (dev) and Windows (CI)."""
import sys
from pathlib import Path

from core import config


def test_data_dir_matches_platform():
    if sys.platform == "win32":
        assert config.DATA_DIR == Path(config.os.environ["APPDATA"]) / "LocalFlow"
        assert config.APP_MODES_PATH.name == "app_modes.windows.json"
        assert config.DEFAULTS["trigger_vk"] == 0x44
        assert config.DEFAULTS["trigger_mods"] == ["ctrl", "alt"]
    else:
        assert config.DATA_DIR == config.ROOT
        assert config.APP_MODES_PATH.name == "app_modes.json"
        assert config.DEFAULTS["trigger_vk"] == 2
        assert config.DEFAULTS["trigger_mods"] == ["ctrl", "alt", "cmd"]


def test_user_paths_derive_from_data_dir():
    for p in (config.CONFIG_PATH, config.HISTORY_DIR,
              config.DICTIONARY_PATH, config.REPLACEMENTS_PATH):
        assert config.DATA_DIR in p.parents or p.parent == config.DATA_DIR


def test_sounds_stay_in_repo():
    assert config.SOUNDS_DIR == config.ROOT / "sounds"
