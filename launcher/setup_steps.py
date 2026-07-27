"""Installing what is missing, one step at a time, in a fixed order."""
import pathlib
import re
import subprocess
import threading

from launcher import plugin_install, setup_checks
from launcher.server_proc import (NO_WINDOW, assign_to_job, clean_line,
                                  close_job, make_kill_on_close_job)

# torch before deps: bitsandbytes pulls torch>=2.3,<3 on its own, and if
# nothing satisfies that yet, pip installs a plain CPU build here that the
# torch step then overwrites with the CUDA one
ORDER = ("venv", "torch", "deps", "plugin", "model", "shortcut")
LABELS = {"venv": "Creating virtual environment",
          "deps": "Installing server dependencies",
          "torch": "Installing PyTorch with CUDA",
          "plugin": "Installing the Aseprite plugin",
          "model": "Downloading the model",
          "shortcut": "Creating a Start Menu shortcut"}
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"
_PROGRESS_RE = re.compile(r"^Progress (\d+) of (\d+)$")


def _friendly_progress(line: str) -> str | None:
    m = _PROGRESS_RE.match(line)
    if not m:
        return None
    done, total = int(m[1]), int(m[2])
    if total <= 0:
        return None
    pct = min(done * 100 // total, 100)
    return f"Downloading... {pct}% ({done / 1e6:.1f}/{total / 1e6:.1f} MB)"


def plan(selected) -> list[str]:
    chosen = set(selected)
    return [step for step in ORDER if step in chosen]


def venv_interpreter(paths) -> pathlib.Path:
    return paths.root / ".venv" / "Scripts" / "python.exe"


def command(step_id: str, paths) -> list[str] | None:
    if step_id == "venv":
        # python -m venv prints nothing at all; stage the silent ~8s of it
        code = (
            "import venv\n"
            "class _V(venv.EnvBuilder):\n"
            "    def setup_python(self, context):\n"
            "        print('Setting up the Python interpreter...', flush=True)\n"
            "        super().setup_python(context)\n"
            "    def _setup_pip(self, context):\n"
            "        print('Bootstrapping pip...', flush=True)\n"
            "        super()._setup_pip(context)\n"
            "    def setup_scripts(self, context):\n"
            "        print('Finishing up...', flush=True)\n"
            "        super().setup_scripts(context)\n"
            f"_V(with_pip=True).create(r'{paths.root / '.venv'}')\n"
        )
        return [str(paths.python), "-u", "-c", code]
    interpreter = str(venv_interpreter(paths))
    if step_id == "deps":
        # --no-compile: python byte-compiles on first import anyway, lazily;
        # pip's own compileall pass otherwise costs ~30s on a package this size
        return [interpreter, "-m", "pip", "install", "--progress-bar", "raw",
                "--no-compile", "-r",
                str(paths.root / "server" / "requirements.txt")]
    if step_id == "torch":
        # bare "torch" reads as satisfied by any installed build, CPU included
        return [interpreter, "-m", "pip", "install", "--progress-bar", "raw",
                "--no-compile", "--upgrade", "torch",
                "--index-url", TORCH_INDEX]
    if step_id == "model":
        # byte-total bar (not file count); Xet's progress lags real transfer,
        # so it's disabled; the repo's standalone ~8 GB checkpoint is unused
        code = (
            "import os\n"
            "os.environ['HF_HUB_DISABLE_XET'] = '1'\n"
            "from huggingface_hub import snapshot_download\n"
            "from tqdm.auto import tqdm as _base_tqdm\n"
            "class _P(_base_tqdm):\n"
            "    _last = None\n"
            "    def display(self, msg=None, pos=None):\n"
            "        if self.total < 1_000_000 or 'incomplete total' not"
            " in (self.desc or ''):\n"
            "            return\n"
            "        pct = min(int(self.n * 100 / self.total), 100)\n"
            "        line = (f'Downloading model files... {pct}% '\n"
            "                f'({self.n/1e6:.0f}/{self.total/1e6:.0f} MB)')\n"
            "        if line != _P._last:\n"
            "            print(line, flush=True)\n"
            "            _P._last = line\n"
            f"snapshot_download({MODEL_ID!r}, cache_dir=r'{paths.models_dir}', "
            "ignore_patterns=['flux-2-klein-4b.safetensors'], tqdm_class=_P)\n"
            "import pathlib\n"
            # marker written only once the download actually finishes
            f"(pathlib.Path(r'{paths.models_dir}') / {setup_checks.MODEL_FOLDER!r} / "
            f"{setup_checks.MODEL_MARKER!r}).write_text('ok')\n"
        )
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
        halted = False
        for step in steps:
            if self.cancelled:
                self.on_event(step, "skipped", "cancelled")
                continue
            if halted:
                self.on_event(step, "skipped", "a previous step failed")
                continue
            self.on_log(f"===== {LABELS[step]} =====")
            self.on_event(step, "running", "")
            ok = self._one(step)
            if self.cancelled:
                self.on_event(step, "cancelled", "stopped")
                halted = True
                continue
            self.on_event(step, "done" if ok else "failed",
                          "" if ok else "see the log")
            if not ok:
                halted = True

    def _one(self, step) -> bool:
        if step == "plugin":
            return self._plugin()
        if step == "shortcut":
            return self._shortcut()
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

    def _shortcut(self) -> bool:
        exe = self.paths.root / "Spriteloom.exe"
        lnk = setup_checks.shortcut_path()
        if not exe.is_file() or not lnk:
            self.on_log("no exe or no Start Menu folder, skipping shortcut")
            return False
        lnk.parent.mkdir(parents=True, exist_ok=True)
        ps = (f'$s = New-Object -ComObject WScript.Shell; '
             f'$sc = $s.CreateShortcut("{lnk}"); '
             f'$sc.TargetPath = "{exe}"; '
             f'$sc.WorkingDirectory = "{self.paths.root}"; $sc.Save()')
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=15,
                creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as e:
            self.on_log(f"could not create shortcut: {e}")
            return False
        if result.returncode != 0:
            self.on_log(f"shortcut creation failed: {result.stderr.strip()}")
            return False
        self.on_log("shortcut created")
        return True

    def _port(self) -> int:
        from server.config import load_port
        return load_port()

    def _spawn(self, cmd) -> bool:
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(self.paths.root), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=NO_WINDOW)
        except OSError as e:
            self.on_log(f"could not start: {e}")
            return False
        # same job trick as the server: cancelling must take pip's children too
        self.job = make_kill_on_close_job()
        assign_to_job(self.job, self.proc.pid)
        # cancel may have fired before the job existed; catch that here
        if self.cancelled:
            job, self.job = self.job, None
            close_job(job)
            return False
        checking = False
        for raw in self.proc.stdout:
            line = clean_line(raw)
            if not line:
                continue
            if line.startswith("Requirement already satisfied"):
                if not checking:
                    self.on_log("Checking installed packages...")
                    checking = True
                continue
            friendly = _friendly_progress(line)
            if friendly:
                self.on_log(friendly, replace=True)
                continue
            if line.startswith("Downloading model files..."):
                self.on_log(line, replace=True)
                continue
            self.on_log(line)
            if line.startswith("Installing collected packages:"):
                self.on_log("Extracting files (can take a few minutes)...")
        code = self.proc.wait()
        job, self.job = self.job, None
        close_job(job)
        return code == 0
