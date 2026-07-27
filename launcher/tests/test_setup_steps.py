import pathlib
import sys
import threading

from launcher import paths, setup_checks, setup_steps


def make_paths(tmp_path):
    return paths.Paths(root=tmp_path, python=pathlib.Path(sys.executable),
                       python_version=(3, 14, 0), aseprite_dir=tmp_path / "ase",
                       models_dir=tmp_path / "models")


def test_plan_keeps_the_fixed_order():
    assert setup_steps.plan(["model", "venv", "torch"]) == \
        ["venv", "torch", "model"]


def test_plan_drops_unknown_ids():
    assert setup_steps.plan(["venv", "nonsense"]) == ["venv"]


def test_command_for_venv(tmp_path):
    cmd = setup_steps.command("venv", make_paths(tmp_path))
    assert cmd[:2] == [sys.executable, "-u"]
    assert "venv" in cmd[-1] and str(tmp_path) in cmd[-1]


def test_venv_command_actually_creates_a_working_venv_with_stages(tmp_path):
    logs = []
    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: None,
                                on_log=lambda line, replace=False: logs.append(line))
    runner.start(["venv"])
    runner.wait(timeout=60)
    assert (tmp_path / ".venv" / "Scripts" / "python.exe").exists()
    assert any("Bootstrapping pip" in l for l in logs)
    assert any("Finishing up" in l for l in logs)


def test_command_for_torch_uses_the_cuda_index(tmp_path):
    cmd = setup_steps.command("torch", make_paths(tmp_path))
    assert "--index-url" in cmd
    assert cmd[-1].endswith("cu128")
    assert "--upgrade" in cmd  # bare "torch" is satisfied by any build else


def test_torch_step_runs_before_deps():
    assert setup_steps.ORDER.index("torch") < setup_steps.ORDER.index("deps")


def test_commands_use_the_raw_progress_bar(tmp_path):
    p = make_paths(tmp_path)
    assert "--progress-bar" in setup_steps.command("deps", p)
    assert "--progress-bar" in setup_steps.command("torch", p)


def test_commands_skip_bytecode_precompilation(tmp_path):
    p = make_paths(tmp_path)
    assert "--no-compile" in setup_steps.command("deps", p)
    assert "--no-compile" in setup_steps.command("torch", p)


def test_model_command_reports_the_shared_byte_total(tmp_path):
    # file-count progress hides multi-minute stalls on a single 7+ GB file;
    # the aggregate byte bar moves continuously regardless of file size
    code = setup_steps.command("model", make_paths(tmp_path))[-1]
    assert "incomplete total" in code
    assert "tqdm_class=_P" in code


def test_model_command_skips_xet_and_the_redundant_checkpoint(tmp_path):
    code = setup_steps.command("model", make_paths(tmp_path))[-1]
    assert "HF_HUB_DISABLE_XET" in code
    assert "flux-2-klein-4b.safetensors" in code


def test_model_command_writes_the_completion_marker(tmp_path):
    # setup_checks treats the snapshot folder's mere existence as insufficient
    # (it appears from the first file downloaded); only this marker means done
    code = setup_steps.command("model", make_paths(tmp_path))[-1]
    assert setup_checks.MODEL_FOLDER in code
    assert setup_checks.MODEL_MARKER in code


def test_friendly_progress_formats_bytes_as_percent():
    assert setup_steps._friendly_progress("Progress 5000000 of 10000000") == \
        "Downloading... 50% (5.0/10.0 MB)"
    assert setup_steps._friendly_progress("Progress 37288845 of 37288845") == \
        "Downloading... 100% (37.3/37.3 MB)"
    assert setup_steps._friendly_progress("not a progress line") is None


def test_progress_lines_replace_in_place(tmp_path):
    logs = []

    def on_log(line, replace=False):
        if replace and logs:
            logs[-1] = line
        else:
            logs.append(line)

    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: None, on_log=on_log)
    script = ("print('Progress 0 of 100')\n"
              "print('Progress 50 of 100')\n"
              "print('Progress 100 of 100')\n"
              "print('Successfully installed pkg')\n")
    runner.commands = {"deps": [sys.executable, "-c", script]}
    runner.start(["deps"])
    runner.wait(timeout=30)
    downloading = [l for l in logs if l.startswith("Downloading")]
    assert downloading == ["Downloading... 100% (0.0/0.0 MB)"]
    assert "Successfully installed pkg" in logs


def test_command_for_plugin_is_not_a_subprocess(tmp_path):
    assert setup_steps.command("plugin", make_paths(tmp_path)) is None


def test_command_for_shortcut_is_not_a_subprocess(tmp_path):
    assert setup_steps.command("shortcut", make_paths(tmp_path)) is None


def test_shortcut_skips_without_the_exe(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    logs = []
    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: None,
                                on_log=lambda line: logs.append(line))
    assert runner._shortcut() is False
    assert any("skipping shortcut" in l for l in logs)


def test_shortcut_creates_a_real_lnk_next_to_the_exe(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    p = make_paths(tmp_path)
    (p.root / "Spriteloom.exe").write_bytes(b"")
    runner = setup_steps.Runner(p, on_event=lambda *a: None,
                                on_log=lambda line: None)
    assert runner._shortcut() is True
    assert setup_checks.shortcut_path().is_file()


def test_failure_skips_dependents(tmp_path):
    events = []
    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: events.append(a),
                                on_log=lambda line: None)
    runner.commands = {"venv": [sys.executable, "-c", "raise SystemExit(1)"],
                       "deps": [sys.executable, "-c", "print('never')"]}
    runner.start(["venv", "deps", "plugin"])
    runner.wait(timeout=30)
    states = {step: state for step, state, _ in events}
    assert states["venv"] == "failed"
    assert states["deps"] == "skipped"
    assert states["plugin"] == "skipped"  # unrelated step, halted all the same


def test_missing_interpreter_fails_cleanly_instead_of_hanging(tmp_path):
    events = []
    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: events.append(a),
                                on_log=lambda line: None)
    runner.commands = {"torch": [str(tmp_path / "no-such-python.exe"), "-V"]}
    runner.start(["torch"])
    runner.wait(timeout=30)
    states = {step: state for step, state, _ in events}
    assert states["torch"] == "failed"


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


def test_already_satisfied_lines_collapse_to_one_note(tmp_path):
    logs = []
    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: None,
                                on_log=lambda line: logs.append(line))
    script = ("import sys\n"
              "for i in range(5):\n"
              "    print('Requirement already satisfied: pkg' + str(i))\n"
              "print('Successfully installed pkg')\n")
    runner.commands = {"deps": [sys.executable, "-c", script]}
    runner.start(["deps"])
    runner.wait(timeout=30)
    checking = [l for l in logs if l.startswith("Checking")]
    assert checking == ["Checking installed packages..."]
    assert "Successfully installed pkg" in logs


def test_installing_collected_packages_gets_an_extraction_note(tmp_path):
    logs = []
    runner = setup_steps.Runner(make_paths(tmp_path),
                                on_event=lambda *a: None,
                                on_log=lambda line: logs.append(line))
    script = "print('Installing collected packages: torch')\n"
    runner.commands = {"deps": [sys.executable, "-c", script]}
    runner.start(["deps"])
    runner.wait(timeout=30)
    assert any(l.startswith("Extracting files") for l in logs)


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
