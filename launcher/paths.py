"""Where things live: detected first, overridden by the settings file."""
import dataclasses
import os
import pathlib
import subprocess
import sys

from server.config import load_settings

MIN_PYTHON = (3, 11)
PYTHON_CANDIDATES = (["py", "-3"], ["python"])
PROBE = "import sys; print(sys.executable + '|%d.%d.%d' % sys.version_info[:3])"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def app_root() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).parent
    return pathlib.Path(__file__).resolve().parent.parent


def run_command(cmd: list[str]) -> str | None:
    """Stdout of a short probe, or None on failure; setup_checks shares it."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def probe_python(command, run=None):
    """(path, version) for a working 3.11+ interpreter, else None."""
    out = (run or run_command)(list(command) + ["-c", PROBE])
    if not out or "|" not in out:
        return None
    exe, _, raw = out.rpartition("|")
    try:
        version = tuple(int(part) for part in raw.split("."))
    except ValueError:
        return None
    if len(version) != 3 or version < MIN_PYTHON:
        return None
    return pathlib.Path(exe), version


def detect_python(run=None):
    for command in PYTHON_CANDIDATES:
        found = probe_python(command, run=run)
        if found:
            return found
    return None


def detect_aseprite_dir() -> pathlib.Path | None:
    base = os.environ.get("APPDATA")
    if not base:
        return None
    folder = pathlib.Path(base) / "Aseprite"
    return folder if folder.is_dir() else None


@dataclasses.dataclass
class Paths:
    root: pathlib.Path
    python: pathlib.Path | None
    python_version: tuple | None
    aseprite_dir: pathlib.Path | None
    models_dir: pathlib.Path


def resolve(settings: dict | None = None, run=None) -> Paths:
    values = settings if settings is not None else load_settings()
    root = pathlib.Path(values["root"]) if values.get("root") else app_root()
    if values.get("python"):
        python = pathlib.Path(values["python"])
        probed = probe_python([str(python)], run=run)
        version = probed[1] if probed else None
    else:
        found = detect_python(run=run)
        python, version = found if found else (None, None)
    if values.get("aseprite_dir"):
        aseprite = pathlib.Path(values["aseprite_dir"])
    else:
        aseprite = detect_aseprite_dir()
    models = (pathlib.Path(values["models_dir"]) if values.get("models_dir")
              else root / "models")
    return Paths(root=root, python=python, python_version=version,
                 aseprite_dir=aseprite, models_dir=models)
