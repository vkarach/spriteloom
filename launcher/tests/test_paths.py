import pathlib

from launcher import paths


def fake_run(answers):
    def run(cmd):
        return answers.get(cmd[0])
    return run


def test_probe_python_accepts_new_enough():
    run = fake_run({"py": r"C:\Python314\python.exe|3.14.0"})
    got = paths.probe_python(["py", "-3"], run=run)
    assert got == (pathlib.Path(r"C:\Python314\python.exe"), (3, 14, 0))


def test_probe_python_rejects_old():
    run = fake_run({"py": r"C:\Python39\python.exe|3.9.7"})
    assert paths.probe_python(["py", "-3"], run=run) is None


def test_probe_python_handles_no_interpreter():
    assert paths.probe_python(["nope"], run=fake_run({})) is None


def test_probe_python_handles_garbage():
    assert paths.probe_python(["py"], run=fake_run({"py": "hello"})) is None


def test_detect_python_falls_back_to_second_candidate():
    run = fake_run({"python": r"C:\P\python.exe|3.12.1"})
    got = paths.detect_python(run=run)
    assert got == (pathlib.Path(r"C:\P\python.exe"), (3, 12, 1))


def test_resolve_prefers_overrides(tmp_path):
    settings = {"root": str(tmp_path), "python": r"C:\custom\python.exe",
                "aseprite_dir": str(tmp_path / "ase"),
                "models_dir": str(tmp_path / "big")}
    got = paths.resolve(settings, run=fake_run({}))
    assert got.root == tmp_path
    assert got.python == pathlib.Path(r"C:\custom\python.exe")
    assert got.aseprite_dir == tmp_path / "ase"
    assert got.models_dir == tmp_path / "big"


def test_resolve_detects_when_not_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (tmp_path / "Aseprite").mkdir()
    run = fake_run({"py": r"C:\P\python.exe|3.13.0"})
    got = paths.resolve({}, run=run)
    assert got.aseprite_dir == tmp_path / "Aseprite"
    assert got.python == pathlib.Path(r"C:\P\python.exe")
    assert got.models_dir == got.root / "models"


def test_resolve_reports_missing_aseprite(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    got = paths.resolve({}, run=fake_run({}))
    assert got.aseprite_dir is None
    assert got.python is None
