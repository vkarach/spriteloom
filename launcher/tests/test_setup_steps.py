import pathlib
import sys
import threading

from launcher import paths, setup_steps


def make_paths(tmp_path):
    return paths.Paths(root=tmp_path, python=pathlib.Path(sys.executable),
                       python_version=(3, 14, 0), aseprite_dir=tmp_path / "ase",
                       models_dir=tmp_path / "models")


def test_plan_keeps_the_fixed_order():
    assert setup_steps.plan(["model", "venv", "torch"]) == \
        ["venv", "torch", "model"]


def test_plan_drops_unknown_ids():
    assert setup_steps.plan(["venv", "nonsense"]) == ["venv"]


def test_dependents_are_transitive():
    assert "model" in setup_steps.dependents("venv")
    assert "torch" in setup_steps.dependents("venv")
    assert setup_steps.dependents("plugin") == set()


def test_command_for_venv(tmp_path):
    cmd = setup_steps.command("venv", make_paths(tmp_path))
    assert cmd[:3] == [sys.executable, "-m", "venv"]


def test_command_for_torch_uses_the_cuda_index(tmp_path):
    cmd = setup_steps.command("torch", make_paths(tmp_path))
    assert "--index-url" in cmd
    assert cmd[-1].endswith("cu128")


def test_command_for_plugin_is_not_a_subprocess(tmp_path):
    assert setup_steps.command("plugin", make_paths(tmp_path)) is None


def test_failure_skips_dependents(tmp_path):
    events = []
    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: events.append(a),
                                on_log=lambda line: None)
    runner.commands = {"venv": [sys.executable, "-c", "raise SystemExit(1)"],
                       "deps": [sys.executable, "-c", "print('never')"]}
    runner.start(["venv", "deps"])
    runner.wait(timeout=30)
    states = {step: state for step, state, _ in events}
    assert states["venv"] == "failed"
    assert states["deps"] == "skipped"


def test_success_runs_every_step(tmp_path):
    events = []
    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: events.append(a),
                                on_log=lambda line: None)
    runner.commands = {"venv": [sys.executable, "-c", "print('made')"],
                       "deps": [sys.executable, "-c", "print('installed')"]}
    runner.start(["venv", "deps"])
    runner.wait(timeout=30)
    states = {step: state for step, state, _ in events}
    assert states["venv"] == "done"
    assert states["deps"] == "done"


def test_cancel_stops_the_run(tmp_path):
    events = []
    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: events.append(a),
                                on_log=lambda line: None)
    runner.commands = {"venv": [sys.executable, "-c",
                                "import time; time.sleep(60)"],
                       "deps": [sys.executable, "-c", "print('never')"]}
    runner.start(["venv", "deps"])
    for _ in range(100):
        if runner.is_running():
            break
        threading.Event().wait(0.05)
    runner.cancel()
    runner.wait(timeout=30)
    states = {step: state for step, state, _ in events}
    assert states["venv"] == "cancelled"
    assert states.get("deps") in ("skipped", "cancelled")
    assert not runner.is_running()
