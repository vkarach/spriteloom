import pathlib

from launcher import paths, setup_checks


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


def test_venv_missing_blocks_deps_and_torch(tmp_path):
    items = by_id(setup_checks.check_all(make_paths(tmp_path),
                                         run=lambda cmd: None))
    assert items["venv"]["state"] == setup_checks.MISSING
    assert items["deps"]["state"] == setup_checks.BLOCKED
    assert items["torch"]["state"] == setup_checks.BLOCKED


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


def test_model_ok_when_snapshot_present(tmp_path):
    p = make_paths(tmp_path)
    snap = p.models_dir / setup_checks.MODEL_FOLDER / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_text("x", encoding="utf-8")
    items = by_id(setup_checks.check_all(p, run=lambda cmd: None))
    assert items["model"]["state"] == setup_checks.OK


def test_model_is_not_required(tmp_path):
    items = by_id(setup_checks.check_all(make_paths(tmp_path),
                                         run=lambda cmd: None))
    assert items["model"]["required"] is False
    assert items["venv"]["required"] is True
