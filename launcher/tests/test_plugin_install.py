import json

from launcher import plugin_install


def test_dest_in_uses_the_extensions_folder(tmp_path):
    dest = plugin_install.dest_in(tmp_path / "Aseprite")
    assert dest == tmp_path / "Aseprite" / "extensions" / "spriteloom"


def make_source(tmp_path, version="0.1.0"):
    src = tmp_path / "plugin"
    src.mkdir(parents=True)
    (src / "package.json").write_text(
        json.dumps({"name": "spriteloom", "version": version}),
        encoding="utf-8")
    (src / "main.lua").write_text("-- entry", encoding="utf-8")
    return src


def test_read_version(tmp_path):
    src = make_source(tmp_path, "1.2.3")
    assert plugin_install.read_version(src) == "1.2.3"


def test_read_version_missing_folder(tmp_path):
    assert plugin_install.read_version(tmp_path / "nope") is None


def test_status_no_aseprite(tmp_path):
    src = make_source(tmp_path)
    assert plugin_install.status(src, None)["state"] == "no_aseprite"


def test_status_missing(tmp_path):
    src = make_source(tmp_path)
    assert plugin_install.status(src, tmp_path / "ext")["state"] == "missing"


def test_status_outdated(tmp_path):
    src = make_source(tmp_path, "0.2.0")
    dest = make_source(tmp_path / "installed", "0.1.0")
    result = plugin_install.status(src, dest)
    assert result["state"] == "outdated"
    assert result["installed"] == "0.1.0"
    assert result["bundled"] == "0.2.0"


def test_status_current(tmp_path):
    src = make_source(tmp_path, "0.1.0")
    dest = make_source(tmp_path / "installed", "0.1.0")
    assert plugin_install.status(src, dest)["state"] == "current"


def test_install_copies_files_and_port(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "ext" / "spriteloom"
    plugin_install.install(src, dest, 9100)
    assert (dest / "main.lua").read_text(encoding="utf-8") == "-- entry"
    written = json.loads((dest / "server.json").read_text(encoding="utf-8"))
    assert written["port"] == 9100


def test_install_skips_subfolders(tmp_path):
    src = make_source(tmp_path)
    (src / "tests").mkdir()
    (src / "tests" / "test_prompt.lua").write_text("-- t", encoding="utf-8")
    dest = tmp_path / "ext" / "spriteloom"
    plugin_install.install(src, dest, 8765)
    assert not (dest / "tests").exists()


def test_install_overwrites_stale_file(tmp_path):
    src = make_source(tmp_path)
    dest = tmp_path / "ext" / "spriteloom"
    dest.mkdir(parents=True)
    (dest / "main.lua").write_text("-- old", encoding="utf-8")
    plugin_install.install(src, dest, 8765)
    assert (dest / "main.lua").read_text(encoding="utf-8") == "-- entry"
