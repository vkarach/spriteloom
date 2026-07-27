import pathlib
import shutil

from launcher import paths, plugin_install, setup_checks


def make_paths(tmp_path, python=True, venv=False):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    if venv:
        scripts = root / ".venv" / "Scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "python.exe").write_text("", encoding="utf-8")
    return paths.Paths(
        root=root,
        python=pathlib.Path(r"C:\P\python.exe") if python else None,
        python_version=(3, 14, 0) if python else None,
        aseprite_dir=None,
        models_dir=root / "models")


def by_id(items):
    return {item["id"]: item for item in items}


def test_python_missing(tmp_path):
    items = by_id(setup_checks.check_all(make_paths(tmp_path, python=False),
                                         run=lambda cmd: None))
    assert items["python"]["state"] == setup_checks.MISSING


def test_python_ok_shows_version(tmp_path):
    items = by_id(setup_checks.check_all(make_paths(tmp_path),
                                         run=lambda cmd: None))
    assert items["python"]["state"] == setup_checks.OK
    assert "3.14.0" in items["python"]["detail"]


def test_venv_missing_still_lets_deps_and_torch_be_selected(tmp_path):
    # the runner creates the venv first regardless, so these stay selectable
    items = by_id(setup_checks.check_all(make_paths(tmp_path),
                                         run=lambda cmd: None))
    assert items["venv"]["state"] == setup_checks.MISSING
    assert items["deps"]["state"] == setup_checks.MISSING
    assert items["torch"]["state"] == setup_checks.MISSING


def test_deps_needs_torch_too(tmp_path):
    # picking deps without torch would still let bitsandbytes pull a plain
    # CPU torch in, with no later step to upgrade it to the CUDA build
    no_venv = by_id(setup_checks.check_all(make_paths(tmp_path),
                                           run=lambda cmd: None))
    assert set(no_venv["deps"]["needs"]) == {"venv", "torch"}
    with_venv = by_id(setup_checks.check_all(make_paths(tmp_path, venv=True),
                                             run=lambda cmd: None))
    assert set(with_venv["deps"]["needs"]) == {"venv", "torch"}


def test_deps_ok_when_imports_succeed(tmp_path):
    items = by_id(setup_checks.check_all(make_paths(tmp_path, venv=True),
                                         run=lambda cmd: "ok"))
    assert items["venv"]["state"] == setup_checks.OK
    assert items["deps"]["state"] == setup_checks.OK


def test_deps_missing_when_import_fails(tmp_path):
    items = by_id(setup_checks.check_all(make_paths(tmp_path, venv=True),
                                         run=lambda cmd: None))
    assert items["deps"]["state"] == setup_checks.MISSING


def test_torch_missing_without_cuda(tmp_path):
    def run(cmd):
        return "2.8.0+cpu" if "torch" in cmd[-1] else "ok"
    items = by_id(setup_checks.check_all(make_paths(tmp_path, venv=True),
                                         run=run))
    assert items["torch"]["state"] == setup_checks.MISSING


def test_torch_ok_with_cuda(tmp_path):
    def run(cmd):
        return "2.8.0+cu128" if "torch" in cmd[-1] else "ok"
    items = by_id(setup_checks.check_all(make_paths(tmp_path, venv=True),
                                         run=run))
    assert items["torch"]["state"] == setup_checks.OK
    assert "12.8" in items["torch"]["detail"]


def test_model_missing_reports_free_space(tmp_path):
    items = by_id(setup_checks.check_all(make_paths(tmp_path),
                                         run=lambda cmd: None))
    assert items["model"]["state"] == setup_checks.MISSING
    assert "free" in items["model"]["detail"]


def test_model_not_ok_from_a_partial_snapshot(tmp_path):
    # a snapshot folder appears from the very first file downloaded; treating
    # its mere existence as "done" reported a stalled/interrupted download
    # as complete, so only the post-download marker counts
    p = make_paths(tmp_path)
    snap = p.models_dir / setup_checks.MODEL_FOLDER / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_text("x", encoding="utf-8")
    items = by_id(setup_checks.check_all(p, run=lambda cmd: None))
    assert items["model"]["state"] == setup_checks.MISSING


def test_model_ok_once_the_marker_is_written(tmp_path):
    p = make_paths(tmp_path)
    folder = p.models_dir / setup_checks.MODEL_FOLDER
    folder.mkdir(parents=True)
    (folder / setup_checks.MODEL_MARKER).write_text("ok", encoding="utf-8")
    items = by_id(setup_checks.check_all(p, run=lambda cmd: None))
    assert items["model"]["state"] == setup_checks.OK


def test_refresh_live_notices_a_deleted_plugin_without_a_full_recheck(tmp_path):
    p = make_paths(tmp_path)
    p = paths.Paths(root=p.root, python=p.python,
                    python_version=p.python_version,
                    aseprite_dir=tmp_path / "ase", models_dir=p.models_dir)
    items = setup_checks.check_all(p, run=lambda cmd: None)
    plugin_dir = plugin_install.dest_in(p.aseprite_dir)
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "package.json").write_text('{"version": "0.1.0"}',
                                             encoding="utf-8")
    fresh = by_id(setup_checks.refresh_live(items, p))
    assert fresh["plugin"]["state"] == setup_checks.OK
    shutil.rmtree(plugin_dir)
    fresh_again = by_id(setup_checks.refresh_live(items, p))
    assert fresh_again["plugin"]["state"] == setup_checks.MISSING


def test_model_is_required(tmp_path):
    # the server won't start without it either; see SERVER_NEEDS in app.py
    items = by_id(setup_checks.check_all(make_paths(tmp_path),
                                         run=lambda cmd: None))
    assert items["model"]["required"] is True
    assert items["venv"]["required"] is True


def test_shortcut_missing_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    items = by_id(setup_checks.check_all(make_paths(tmp_path),
                                         run=lambda cmd: None))
    assert items["shortcut"]["state"] == setup_checks.MISSING
    assert items["shortcut"]["required"] is False


def test_shortcut_ok_once_the_lnk_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    lnk = setup_checks.shortcut_path()
    lnk.parent.mkdir(parents=True)
    lnk.write_text("", encoding="utf-8")
    items = by_id(setup_checks.check_all(make_paths(tmp_path),
                                         run=lambda cmd: None))
    assert items["shortcut"]["state"] == setup_checks.OK
