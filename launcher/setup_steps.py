"""Installing what is missing, one step at a time, in a fixed order."""
import pathlib
import subprocess
import threading

from launcher import plugin_install
from launcher.server_proc import (NO_WINDOW, assign_to_job, clean_line,
                                  close_job, make_kill_on_close_job)

ORDER = ("venv", "deps", "torch", "plugin", "model")
NEEDS = {"venv": (), "deps": ("venv",), "torch": ("venv",), "plugin": (),
         "model": ("deps",)}
LABELS = {"venv": "Creating virtual environment",
          "deps": "Installing server dependencies",
          "torch": "Installing PyTorch with CUDA",
          "plugin": "Installing the Aseprite plugin",
          "model": "Downloading the model"}
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"


def plan(selected) -> list[str]:
    chosen = set(selected)
    return [step for step in ORDER if step in chosen]


def dependents(step_id: str) -> set[str]:
    found = set()
    pending = [step_id]
    while pending:
        current = pending.pop()
        for step, needs in NEEDS.items():
            if current in needs and step not in found:
                found.add(step)
                pending.append(step)
    return found


def venv_interpreter(paths) -> pathlib.Path:
    return paths.root / ".venv" / "Scripts" / "python.exe"


def command(step_id: str, paths) -> list[str] | None:
    if step_id == "venv":
        return [str(paths.python), "-m", "venv", str(paths.root / ".venv")]
    interpreter = str(venv_interpreter(paths))
    if step_id == "deps":
        return [interpreter, "-m", "pip", "install", "-r",
                str(paths.root / "server" / "requirements.txt")]
    if step_id == "torch":
        return [interpreter, "-m", "pip", "install", "torch",
                "--index-url", TORCH_INDEX]
    if step_id == "model":
        code = ("from huggingface_hub import snapshot_download; "
                f"snapshot_download({MODEL_ID!r}, cache_dir=r'"
                f"{paths.models_dir}')")
        return [interpreter, "-u", "-c", code]
    return None  # the plugin step is a plain function call


class Runner:
    def __init__(self, paths, on_event, on_log):
        self.paths = paths
        self.on_event = on_event
        self.on_log = on_log
        self.commands = {}
        self.proc = None
        self.job = None
        self.cancelled = False
        self.thread = None

    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, ids) -> None:
        if self.is_running():
            return
        self.cancelled = False
        self.thread = threading.Thread(target=self._run, args=(plan(ids),),
                                       daemon=True)
        self.thread.start()

    def wait(self, timeout=None) -> None:
        if self.thread:
            self.thread.join(timeout)

    def cancel(self) -> None:
        self.cancelled = True
        job, self.job = self.job, None
        close_job(job)

    def _run(self, steps) -> None:
        skip = set()
        for step in steps:
            if self.cancelled:
                self.on_event(step, "skipped", "cancelled")
                continue
            if step in skip:
                self.on_event(step, "skipped", "a step it needs failed")
                continue
            self.on_log(f"-- {LABELS[step]} --")
            self.on_event(step, "running", "")
            ok = self._one(step)
            if self.cancelled:
                self.on_event(step, "cancelled", "stopped")
                skip |= dependents(step)
                continue
            self.on_event(step, "done" if ok else "failed",
                          "" if ok else "see the log")
            if not ok:
                skip |= dependents(step)

    def _one(self, step) -> bool:
        if step == "plugin":
            return self._plugin()
        cmd = self.commands.get(step) or command(step, self.paths)
        return self._spawn(cmd)

    def _plugin(self) -> bool:
        folder = self.paths.aseprite_dir
        if not folder:
            self.on_log("no Aseprite folder to install into")
            return False
        try:
            plugin_install.install(plugin_install.source_dir(),
                                   plugin_install.dest_in(folder),
                                   self._port())
        except OSError as e:
            self.on_log(f"copy failed: {e}")
            return False
        self.on_log("plugin copied, restart Aseprite")
        return True

    def _port(self) -> int:
        from server.config import load_port
        return load_port()

    def _spawn(self, cmd) -> bool:
        self.proc = subprocess.Popen(
            cmd, cwd=str(self.paths.root), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", creationflags=NO_WINDOW)
        # same job trick as the server: cancelling must take pip's children too
        self.job = make_kill_on_close_job()
        assign_to_job(self.job, self.proc.pid)
        # cancel may have fired before the job existed; catch that here
        if self.cancelled:
            job, self.job = self.job, None
            close_job(job)
            return False
        for raw in self.proc.stdout:
            line = clean_line(raw)
            if line:
                self.on_log(line)
        code = self.proc.wait()
        job, self.job = self.job, None
        close_job(job)
        return code == 0
