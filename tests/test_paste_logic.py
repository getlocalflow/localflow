from windows.win_paste import build_key_sequence, VK_CONTROL, VK_V


def test_ctrl_v_sequence_order():
    seq = build_key_sequence()
    # (vk, is_keyup) pairs: ctrl down, v down, v up, ctrl up
    assert seq == [(VK_CONTROL, False), (VK_V, False),
                   (VK_V, True), (VK_CONTROL, True)]
