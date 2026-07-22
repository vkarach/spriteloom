"""The launcher window: config, plugin install and server process, glued."""
import pathlib
import sys
import threading

import webview

from launcher import plugin_install, server_proc
from server.config import HOST, load_port, save_port

VERSION = "0.1.0"
TITLE = "SpriteForge"
WIDTH = 520
HEIGHT_COMPACT = 410
HEIGHT_WITH_LOG = 700
# below this the plugin row and the status line start colliding
MIN_SIZE = (430, 380)
POLL_SECONDS = 1.5
OFFLINE = {"state": "offline", "progress": 0.0, "stage": None}
NO_VENV = ("No .venv found in {root}. "
           "Build the environment the way the README describes.")


def app_root() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).parent
    return pathlib.Path(__file__).resolve().parent.parent


def ui_file() -> str:
    if getattr(sys, "frozen", False):
        base = pathlib.Path(sys._MEIPASS) / "ui" / "index.html"
    else:
        base = pathlib.Path(__file__).resolve().parent / "ui" / "index.html"
    return str(base)


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
        self.window = None
        if server_proc.venv_python(self.root) is None:
            self._say(NO_VENV.format(root=self.root), bad=True)
        threading.Thread(target=self._poll, daemon=True).start()

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

    def set_log_open(self, is_open: bool) -> None:
        # the log is the only tall part; the window grows only when it is shown
        if self.window:
            self.window.resize(WIDTH,
                               HEIGHT_WITH_LOG if is_open else HEIGHT_COMPACT)

    def toggle_server(self) -> dict:
        if self.proc.is_alive():
            self.stopped_by_user = True
            self.proc.stop()
            self.health = dict(OFFLINE)
            self._say("")
            return self._snapshot()
        if server_proc.venv_python(self.root) is None:
            self._say(NO_VENV.format(root=self.root), bad=True)
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
        return "", "Server stopped", 0.0

    def _snapshot(self) -> dict:
        tone, label, progress = self._server_view()
        text, warn, action = self._plugin_view()
        return {
            "version": VERSION,
            "url": f"ws://{HOST}:{self.port}",
            "tone": tone,
            "label": label,
            "progress": progress,
            "running": self.proc.is_alive(),
            "can_start": server_proc.venv_python(self.root) is not None,
            "hint": self.hint,
            "hint_bad": self.hint_bad,
            "plugin_text": text,
            "plugin_warn": warn,
            "plugin_action": action,
            "log": "\n".join(self.proc.lines[-200:]),
        }


def main() -> None:
    api = Api()
    window = webview.create_window(TITLE, ui_file(), js_api=api,
                                   width=WIDTH, height=HEIGHT_COMPACT,
                                   min_size=MIN_SIZE,
                                   background_color="#14161c")
    api.window = window
    if not webview2_present():
        window.load_html(
            "<body style='background:#14161c;color:#d3d7e0;"
            "font:13px monospace;padding:24px;line-height:1.5'>"
            "Microsoft Edge WebView2 is required.<br><br>Get it at "
            "developer.microsoft.com/microsoft-edge/webview2, "
            "then start SpriteForge again.</body>")
    window.events.closing += lambda: api.proc.stop()
    webview.start()


if __name__ == "__main__":
    main()
