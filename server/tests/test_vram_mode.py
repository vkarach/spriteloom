from server.instruct import chunk_size, resolve_mode

GIB = 1024 ** 3


def test_explicit_mode_ignores_vram():
    for mode in ("bf16", "offload"):
        assert resolve_mode(mode, 4 * GIB) == mode
        assert resolve_mode(mode, 24 * GIB) == mode


def test_auto_picks_bf16_on_a_12gb_card():
    assert resolve_mode("auto", 11.8 * GIB) == "bf16"


def test_auto_picks_offload_on_an_8gb_card():
    assert resolve_mode("auto", 7.6 * GIB) == "offload"


def test_auto_picks_offload_below_8gb():
    assert resolve_mode("auto", 4 * GIB) == "offload"


def test_bf16_keeps_the_current_chunk_sizes():
    assert chunk_size("bf16", "t2i") == 4
    assert chunk_size("bf16", "edit") == 2


def test_low_vram_modes_shrink_chunks():
    assert chunk_size("offload", "t2i") == 2
    assert chunk_size("offload", "edit") == 1
