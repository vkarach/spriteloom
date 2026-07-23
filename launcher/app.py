"""The launcher window: config, plugin install and server process, glued."""
import pathlib
import sys
import threading
import webbrowser

import webview

from launcher import paths, plugin_install, server_proc, setup_checks, \
    setup_steps
from launcher.paths import app_root
from server.config import (HOST, VRAM_MODES, load_port, load_settings,
                           load_vram_mode, save_port, save_settings,
                           save_vram_mode)

VERSION = "0.1.0"
TITLE = "Spriteloom"
NARROW = 476
# the log panel adds this to the width when it's open
LOGW = 360
WIDE = NARROW + LOGW
# SetWindowPos sizes the outer window, not the client area; measured via
# CDP, the border eats exactly this many px at any width
CHROME_W = 16
# ignore sub-pixel height jitter so DPI rounding never ping-pongs the window
FIT_DEADBAND = 3
# the page measures itself and drives the real height from here
HEIGHT_COMPACT = 360
MIN_HEIGHT = 240
MIN_SIZE = (NARROW, MIN_HEIGHT)
SERVER_NEEDS = ("venv", "deps", "torch")
MAX_SCREEN_FRACTION = 0.9
POLL_SECONDS = 1.5
OFFLINE = {"state": "offline", "progress": 0.0, "stage": None}
NO_VENV = ("No .venv found in {root}. "
           "Build the environment the way the README describes.")

# must not be an Api attribute: pywebview walking it into js_api wedges the UI thread
_window = None


def _screen_height(default: int = 1080) -> int:
    try:
        return int(webview.screens[0].height)
    except (AttributeError, IndexError, TypeError, ValueError):
        return default


def _ui(name: str) -> str:
    if getattr(sys, "frozen", False):
        base = pathlib.Path(sys._MEIPASS) / "ui" / name
    else:
        base = pathlib.Path(__file__).resolve().parent / "ui" / name
    return str(base)


def ui_file() -> str:
    return _ui("index.html")


def webview2_present() -> bool:
    if sys.platform != "win32":
        return True
    import winreg
    key = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
           r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key):
                return True
        except OSError:
            continue
    return False


class Api:
    def __init__(self, root: pathlib.Path | None = None):
        self.root = pathlib.Path(root) if root else app_root()
        self.port = load_port()
        self.proc = self._new_proc()
        self.health = dict(OFFLINE)
        self.stopped_by_user = False
        self.hint = ""
        self.hint_bad = False
        self.height = HEIGHT_COMPACT
        self.width = NARROW
        self.paths = paths.resolve()
        self.items: list[dict] = []
        self.checking = False
        self.setup_log: list[str] = []
        self.step_state: dict[str, tuple] = {}
        self.was_running = False
        self.runner = setup_steps.Runner(self.paths, self._step_event,
                                         self._setup_log)
        if server_proc.venv_python(self.root) is None:
            self._say(NO_VENV.format(root=self.root), bad=True)
        threading.Thread(target=self._poll, daemon=True).start()
        self._start_check()

    def _new_proc(self):
        return server_proc.ServerProcess(self.root, self.port,
                                         on_log=lambda line: None)

    def _say(self, text: str, bad: bool = False) -> None:
        self.hint = text
        self.hint_bad = bad

    def _poll(self) -> None:
        # polling off the UI thread so a click never waits on a socket timeout
        while True:
            self.health = (server_proc.probe(self.port, timeout=1.0)
                           if self.proc.is_alive() else dict(OFFLINE))
            threading.Event().wait(POLL_SECONDS)

    # ---- called from JS

    def state(self) -> dict:
        return self._snapshot()

    def resize(self, delta: int) -> None:
        """Only the height follows the page; width moves via set_width."""
        if not _window:
            return
        d = int(delta)
        # grow instantly; shrink only past the deadband to avoid DPI jitter
        if d > 0 or d < -FIT_DEADBAND:
            cap = int(_screen_height() * MAX_SCREEN_FRACTION)
            target = min(max(MIN_HEIGHT, self.height + d), cap)
            if target != self.height:
                self.height = target
                _window.resize(self.width + CHROME_W, target)

    def set_width(self, width: int) -> None:
        """A one-shot width jump (log panel open/close), never continuous."""
        if not _window:
            return
        w = int(width)
        if w != self.width:
            self.width = w
            _window.resize(w + CHROME_W, self.height)

    def open_url(self, url: str) -> None:
        if str(url).startswith(("http://", "https://")):
            webbrowser.open(url)

    def _server_ready(self) -> bool:
        state = {it["id"]: it["state"] for it in self.items}
        return bool(self.items) and all(
            state.get(k) == setup_checks.OK for k in SERVER_NEEDS)

    def toggle_server(self) -> dict:
        if self.proc.is_alive():
            self.stopped_by_user = True
            self.proc.stop()
            self.proc.lines.append("-- stopped --")
            self.health = dict(OFFLINE)
            self._say("")
            return self._snapshot()
        if server_proc.venv_python(self.root) is None:
            self._say(NO_VENV.format(root=self.root), bad=True)
            return self._snapshot()
        if not self._server_ready():
            self._say("Finish Setup first: install the missing pieces.",
                      bad=True)
            return self._snapshot()
        free = server_proc.pick_port(self.port)
        if free != self.port:
            self._say(f"Port {self.port} was busy, moved to {free}. "
                      "Reinstall the plugin so it learns the new port.")
            self.port = free
            save_port(free)
        else:
            self._say("")
        self.stopped_by_user = False
        self.proc = self._new_proc()
        self.proc.start()
        return self._snapshot()

    def set_vram_mode(self, mode: str) -> dict:
        if mode not in VRAM_MODES:
            return self._snapshot()
        save_vram_mode(mode)
        # the server reads the mode once, while loading the model
        if self.proc.is_alive():
            self._say("VRAM mode saved. Restart the server to apply it.")
        else:
            self._say("")
        return self._snapshot()

    # ---- setup wizard

    def _setup_log(self, line: str) -> None:
        self.setup_log.append(line)
        del self.setup_log[:-200]

    def _step_event(self, step_id: str, state: str, detail: str) -> None:
        self.step_state[step_id] = (state, detail)

    def _start_check(self) -> None:
        # deps and torch probes import torch, seconds each; keep it off the UI
        if self.checking:
            return
        self.checking = True

        def work():
            resolved = paths.resolve()
            items = setup_checks.check_all(resolved)
            self.paths = resolved
            self.items = items
            self.checking = False

        threading.Thread(target=work, daemon=True).start()

    def recheck(self) -> dict:
        self._start_check()
        return self.setup_state()

    def setup_state(self) -> dict:
        running = self.runner.is_running()
        # a run that just ended must leave the list showing reality
        if self.was_running and not running:
            self.was_running = False
            self._start_check()
        rows = []
        for item in self.items:
            state, detail = self.step_state.get(item["id"], (None, ""))
            rows.append({**item, "step_state": state, "step_detail": detail})
        cold = any(item["required"] and item["state"] != setup_checks.OK
                   for item in self.items)
        saved = load_settings()
        overrides = [k for k in ("root", "python", "aseprite_dir",
                                 "models_dir") if saved.get(k)]
        return {"items": rows, "paths": self._paths_view(), "running": running,
                "checking": self.checking, "cold": cold,
                "overrides": overrides, "vram_mode": load_vram_mode(),
                "log": "\n".join(self.setup_log[-200:])}

    def _paths_view(self) -> dict:
        p = self.paths
        return {"root": str(p.root),
                "python": str(p.python) if p.python else "",
                "aseprite_dir": str(p.aseprite_dir) if p.aseprite_dir else "",
                "models_dir": str(p.models_dir)}

    def install(self, ids) -> dict:
        if self.runner.is_running():
            return self.setup_state()
        self.step_state = {}
        self.setup_log = []
        self.runner = setup_steps.Runner(self.paths, self._step_event,
                                         self._setup_log)
        self.runner.start(list(ids))
        self.was_running = True
        return self.setup_state()

    def cancel_install(self) -> dict:
        self.runner.cancel()
        self._setup_log("-- cancelled --")
        return self.setup_state()

    def choose_path(self, kind: str) -> dict:
        if _window is None or kind not in ("root", "python", "aseprite_dir",
                                           "models_dir"):
            return self.setup_state()
        mode = (webview.OPEN_DIALOG if kind == "python"
                else webview.FOLDER_DIALOG)
        picked = _window.create_file_dialog(mode)
        if picked:
            save_settings({kind: str(picked[0])})
            self.paths = paths.resolve()
            self.step_state = {}
        return self.recheck()

    def reset_path(self, kind: str) -> dict:
        # drop the manual override so this path auto-detects again
        if kind in ("root", "python", "aseprite_dir", "models_dir"):
            save_settings({kind: None})
            self.paths = paths.resolve()
            self.step_state = {}
        return self.recheck()

    def install_plugin(self) -> dict:
        dest = plugin_install.extensions_dir()
        if dest is None:
            self._say("No Aseprite found in %APPDATA%. Is it installed?",
                      bad=True)
            return self._snapshot()
        try:
            plugin_install.install(plugin_install.source_dir(), dest, self.port)
        except OSError as e:
            self._say(f"Could not copy the plugin: {e}", bad=True)
            return self._snapshot()
        self._say("Plugin installed. Restart Aseprite.")
        return self._snapshot()

    # ---- state assembly

    def _plugin_view(self) -> tuple:
        info = plugin_install.status(plugin_install.source_dir(),
                                     plugin_install.extensions_dir())
        state = info["state"]
        if state == "no_aseprite":
            return "no Aseprite found", True, "Install"
        if state == "missing":
            return "not installed", True, "Install"
        if state == "outdated":
            return f"{info['installed']}, {info['bundled']} available", True, \
                "Update"
        return f"✓ {info['installed']}", False, "Reinstall"

    def _server_view(self) -> tuple:
        if self.proc.is_alive():
            health = self.health
            if health["state"] == "ready":
                return "ready", "Ready", 0.0
            if health["state"] == "loading":
                stage = health["stage"] or "model"
                return "busy", f"Loading: {stage}", health["progress"]
            return "busy", "Starting", 0.0
        if self.proc.proc is not None and not self.stopped_by_user:
            return "error", "Server crashed", 0.0
        if self.proc.proc is None:
            # while the background check is still running self.items is
            # empty, which would otherwise read as "not ready" for a split
            # second even on a machine that's perfectly set up
            if self.items and not self._server_ready():
                return "", "Need to configure", 0.0
            return "", "Not started", 0.0
        return "", "Server stopped", 0.0

    def _snapshot(self) -> dict:
        tone, label, progress = self._server_view()
        text, warn, action = self._plugin_view()
        cold = any(item["required"] and item["state"] != setup_checks.OK
                   for item in self.items)
        return {
            "version": VERSION,
            "cold": cold,
            "url": f"ws://{HOST}:{self.port}",
            "tone": tone,
            "label": label,
            "progress": progress,
            "running": self.proc.is_alive(),
            "can_start": self._server_ready(),
            "hint": self.hint,
            "hint_bad": self.hint_bad,
            "vram_mode": load_vram_mode(),
            "plugin_text": text,
            "plugin_warn": warn,
            "plugin_action": action,
            "log": "\n".join(self.proc.lines[-200:]),
        }


def main() -> None:
    global _window
    api = Api()
    window = webview.create_window(TITLE, ui_file(), js_api=api,
                                   width=NARROW + CHROME_W, height=HEIGHT_COMPACT,
                                   min_size=MIN_SIZE, resizable=False,
                                   background_color="#14161c")
    _window = window

    window.events.closing += lambda: api.proc.stop()

    if not webview2_present():
        window.load_html(
            "<body style='background:#14161c;color:#d3d7e0;"
            "font:13px monospace;padding:24px;line-height:1.5'>"
            "Microsoft Edge WebView2 is required.<br><br>Get it at "
            "developer.microsoft.com/microsoft-edge/webview2, "
            "then start Spriteloom again.</body>")
    webview.start()


if __name__ == "__main__":
    main()
