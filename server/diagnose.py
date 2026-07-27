"""Loader self-check for when the server crashes on start.

Run: .venv\\Scripts\\python -m server.diagnose
"""
import importlib.metadata as md
import sys

from server.config import load_models_dir
from server.instruct import (memory_status, safetensors_expected_size,
                             snapshot_dir)

PACKAGES = ("torch", "safetensors", "diffusers", "transformers",
            "accelerate", "bitsandbytes")


def gb(n: float) -> str:
    return f"{n / 1024 ** 3:.2f} GB"


def show_versions() -> None:
    print("== versions ==")
    print(f"  python:       {sys.version.split()[0]}")
    for name in PACKAGES:
        try:
            print(f"  {name + ':':13} {md.version(name)}")
        except md.PackageNotFoundError:
            print(f"  {name + ':':13} (not installed)")


def show_memory() -> None:
    print("== memory ==")
    mem = memory_status()
    if mem is None:
        print("  (not Windows; skipped)")
        return
    print(f"  RAM total:   {gb(mem['total_ram'])}")
    print(f"  RAM free:    {gb(mem['free_ram'])}")
    print(f"  commit max:  {gb(mem['total_commit'])}  (RAM + page file)")
    print(f"  commit free: {gb(mem['free_commit'])}")


def show_files() -> object | None:
    """Print each weight file against its header size; return the transformer if complete."""
    models_dir = load_models_dir()
    print(f"== model files (models_dir = {models_dir!r}) ==")
    snap = snapshot_dir(models_dir)
    if snap is None:
        print("  not downloaded; run Setup")
        return None
    transformer = None
    ok = True
    for path in sorted(snap.rglob("*.safetensors")):
        actual = path.stat().st_size
        try:
            expected = safetensors_expected_size(path)
        except (OSError, ValueError):
            print(f"  UNREADABLE  {path.name}")
            ok = False
            continue
        rel = path.relative_to(snap).as_posix()
        tag = "ok" if actual >= expected else f"TRUNCATED (want {expected})"
        print(f"  {tag:28} {actual:>13} bytes  {rel}")
        ok = ok and actual >= expected
        if path.parent.name == "transformer":
            transformer = path
    return transformer if ok else None


def load_transformer_alone(path) -> None:
    print("== isolated load ==")
    if path is None:
        print("  skipped (files missing or truncated -> re-download the model)")
        return
    print("  loading the transformer alone...")
    from safetensors.torch import load_file
    load_file(str(path))
    print("  transformer loaded with no crash -> files are fine")
    print("  a crash in the full run is then memory: close apps or raise the "
          "Windows page file")


def main() -> None:
    show_versions()
    show_memory()
    transformer = show_files()
    load_transformer_alone(transformer)


if __name__ == "__main__":
    main()
