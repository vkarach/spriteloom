import json

from server import config


def test_default_when_file_missing(tmp_path):
    assert config.load_port(tmp_path / "nope.json") == 8765


def test_round_trip(tmp_path):
    path = tmp_path / "config.json"
    config.save_port(9100, path)
    assert config.load_port(path) == 9100


def test_broken_json_falls_back(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert config.load_port(path) == 8765


def test_out_of_range_falls_back(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"port": 70000}), encoding="utf-8")
    assert config.load_port(path) == 8765


def test_save_creates_parent_dir(tmp_path):
    path = tmp_path / "deep" / "config.json"
    config.save_port(8765, path)
    assert path.exists()


def test_save_port_keeps_foreign_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"port": 8765, "vram_mode": "fp8"}),
                    encoding="utf-8")
    config.save_port(9100, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["port"] == 9100
    assert data["vram_mode"] == "fp8"


def test_port_and_vram_mode_coexist(tmp_path):
    path = tmp_path / "config.json"
    config.save_vram_mode("offload", path)
    config.save_port(9100, path)
    assert config.load_vram_mode(path) == "offload"
    assert config.load_port(path) == 9100


def test_save_vram_mode_rejects_unknown(tmp_path):
    path = tmp_path / "config.json"
    config.save_vram_mode("offload", path)
    config.save_vram_mode("turbo", path)
    assert config.load_vram_mode(path) == "offload"


def test_save_vram_mode_rejects_disabled_fp8(tmp_path):
    path = tmp_path / "config.json"
    config.save_vram_mode("fp8", path)
    assert config.load_vram_mode(path) == "auto"


def test_load_settings_fills_missing_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"port": 9100}), encoding="utf-8")
    settings = config.load_settings(path)
    assert settings["port"] == 9100
    assert settings["root"] is None
    assert set(settings) == set(config.SETTING_KEYS)


def test_save_settings_merges(tmp_path):
    path = tmp_path / "config.json"
    config.save_settings({"root": "F:\\proj"}, path)
    config.save_settings({"python": "C:\\py.exe"}, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["root"] == "F:\\proj"
    assert data["python"] == "C:\\py.exe"


def test_models_dir_default_and_override(tmp_path):
    path = tmp_path / "config.json"
    assert config.load_models_dir(path) == "models"
    config.save_settings({"models_dir": "D:\\big"}, path)
    assert config.load_models_dir(path) == "D:\\big"


def test_load_settings_on_broken_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    settings = config.load_settings(path)
    assert settings["port"] == 8765
    assert settings["models_dir"] is None
