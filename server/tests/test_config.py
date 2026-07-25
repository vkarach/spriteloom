import json

from server import config


def _write(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_missing_file_defaults_to_auto(tmp_path):
    assert config.load_vram_mode(tmp_path / "nope.json") == "auto"


def test_missing_key_defaults_to_auto(tmp_path):
    assert config.load_vram_mode(_write(tmp_path, {"port": 8765})) == "auto"


def test_valid_modes_pass_through(tmp_path):
    for mode in ("auto", "bf16", "offload"):
        p = _write(tmp_path, {"vram_mode": mode})
        assert config.load_vram_mode(p) == mode


def test_invalid_value_defaults_to_auto(tmp_path):
    assert config.load_vram_mode(_write(tmp_path, {"vram_mode": "int8"})) \
        == "auto"


def test_disabled_fp8_falls_back_to_auto(tmp_path):
    # fp8 gave no real speedup without torch.compile and stayed VRAM-tight;
    # dropped from the option list, so a stale saved value must not stick
    assert config.load_vram_mode(_write(tmp_path, {"vram_mode": "fp8"})) \
        == "auto"


def test_non_string_defaults_to_auto(tmp_path):
    assert config.load_vram_mode(_write(tmp_path, {"vram_mode": 8})) == "auto"


def test_malformed_json_defaults_to_auto(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json", encoding="utf-8")
    assert config.load_vram_mode(p) == "auto"
