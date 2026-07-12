from windows.win_hotkey import combo_matches


def test_exact_combo_matches():
    assert combo_matches(0x44, {"ctrl", "alt"}, 0x44, ["ctrl", "alt"])


def test_extra_modifier_still_matches_when_shift():
    # Shift is the raw-mode overlay, never part of the combo itself
    assert combo_matches(0x44, {"ctrl", "alt", "shift"}, 0x44, ["ctrl", "alt"])


def test_missing_modifier_rejects():
    assert not combo_matches(0x44, {"ctrl"}, 0x44, ["ctrl", "alt"])


def test_wrong_key_rejects():
    assert not combo_matches(0x45, {"ctrl", "alt"}, 0x44, ["ctrl", "alt"])


def test_superfluous_win_key_rejects():
    # A stray Win key means it is some OTHER shortcut; do not fire
    assert not combo_matches(0x44, {"ctrl", "alt", "win"}, 0x44, ["ctrl", "alt"])
