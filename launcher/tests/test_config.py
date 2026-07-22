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
