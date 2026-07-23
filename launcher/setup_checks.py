"""What is already in place. Detection only, never a side effect."""
import shutil

from launcher import plugin_install
from launcher.paths import MIN_PYTHON, run_command
from launcher.server_proc import venv_python

OK = "ok"
MISSING = "missing"
BLOCKED = "blocked"

MODEL_FOLDER = "models--black-forest-labs--FLUX.2-klein-4B"
MODEL_GB = 15
# metadata only, not a real import -- importing torch/diffusers costs seconds
DEPS_PROBE = ("import importlib.metadata as m; "
              "[m.version(d) for d in ('websockets','diffusers','transformers',"
              "'accelerate','peft','bitsandbytes','Pillow','numpy','scipy')]; "
              "print('ok')")
TORCH_PROBE = "import importlib.metadata as m; print(m.version('torch'))"


def _cuda_tag(version):
    # torch's local version tag carries the CUDA build, e.g. 2.4.1+cu124 -> 12.4
    if not version or "+cu" not in version:
        return None
    digits = "".join(c for c in version.split("+cu", 1)[1] if c.isdigit())
    return f"{digits[:-1]}.{digits[-1]}" if len(digits) >= 2 else None


def _item(item_id, label, state, detail, required=True, needs=()):
    return {"id": item_id, "label": label, "state": state, "detail": detail,
            "required": required, "needs": list(needs)}


def _free_gb(folder) -> int:
    probe = folder
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free // 2**30
    except OSError:
        return 0


def check_all(paths, run=None) -> list[dict]:
    runner = run or run_command
    items = []

    version = paths.python_version
    if paths.python and (version is None or version >= MIN_PYTHON):
        shown = ".".join(str(part) for part in version) if version else "found"
        items.append(_item("python", "Python 3.11+", OK, shown))
        python_ok = True
    else:
        items.append(_item("python", "Python 3.11+", MISSING, "not found"))
        python_ok = False

    interpreter = venv_python(paths.root)
    if interpreter:
        items.append(_item("venv", "Virtual environment", OK, ".venv",
                           needs=["python"]))
    else:
        state = MISSING if python_ok else BLOCKED
        items.append(_item("venv", "Virtual environment", state, "missing",
                           needs=["python"]))

    if not interpreter:
        items.append(_item("deps", "Server dependencies", BLOCKED,
                           "needs the environment", needs=["venv"]))
        items.append(_item("torch", "PyTorch with CUDA", BLOCKED,
                           "needs the environment", needs=["venv"]))
    else:
        got = runner([str(interpreter), "-c", DEPS_PROBE])
        items.append(_item("deps", "Server dependencies",
                           OK if got else MISSING,
                           "installed" if got else "missing", needs=["venv"]))
        got = runner([str(interpreter), "-c", TORCH_PROBE])
        cuda = _cuda_tag(got)
        if cuda:
            items.append(_item("torch", "PyTorch with CUDA", OK,
                               f"CUDA {cuda}", needs=["venv"]))
        else:
            detail = "no CUDA" if got else "missing"
            items.append(_item("torch", "PyTorch with CUDA", MISSING, detail,
                               needs=["venv"]))

    dest = (plugin_install.dest_in(paths.aseprite_dir)
            if paths.aseprite_dir else None)
    info = plugin_install.status(plugin_install.source_dir(), dest)
    if info["state"] == "current":
        items.append(_item("plugin", "Aseprite plugin", OK, info["installed"]))
    elif info["state"] == "no_aseprite":
        items.append(_item("plugin", "Aseprite plugin", BLOCKED,
                           "no Aseprite found"))
    else:
        detail = ("not installed" if info["state"] == "missing"
                  else f"{info['installed']}, {info['bundled']} available")
        items.append(_item("plugin", "Aseprite plugin", MISSING, detail))

    snapshots = paths.models_dir / MODEL_FOLDER / "snapshots"
    have = snapshots.is_dir() and any(p.is_dir() for p in snapshots.iterdir())
    if have:
        items.append(_item("model", f"Model, {MODEL_GB} GB", OK, "downloaded",
                           required=False, needs=["deps"]))
    else:
        items.append(_item("model", f"Model, {MODEL_GB} GB", MISSING,
                           f"{_free_gb(paths.models_dir)} GB free",
                           required=False, needs=["deps"]))
    return items
