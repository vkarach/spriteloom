"""Server port: one file read by both the launcher and the server."""
import json
import os
import pathlib

DEFAULT_PORT = 8765
HOST = "127.0.0.1"


def config_path() -> pathlib.Path:
    base = os.environ.get("APPDATA")
    root = pathlib.Path(base) if base else pathlib.Path.home()
    return root / "SpriteForge" / "config.json"


def load_port(path: pathlib.Path | None = None) -> int:
    target = path or config_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))["port"]
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_PORT
    if isinstance(value, int) and not isinstance(value, bool) \
            and 1024 <= value <= 65535:
        return value
    return DEFAULT_PORT


def save_port(port: int, path: pathlib.Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"port": port}, indent=2), encoding="utf-8")
