import asyncio
import json
import pathlib
import socket
import subprocess
import sys
import threading

import pytest
import websockets

from launcher import server_proc


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_clean_line_keeps_last_tqdm_state():
    raw = "Loading weights:  10%|# | 40/398\rLoading weights: 100%|##| 398/398\n"
    assert server_proc.clean_line(raw) == "Loading weights: 100%|##| 398/398"


def test_clean_line_strips_ansi():
    assert server_proc.clean_line("done\x1b[A\n") == "done"


def test_clean_line_blank_stays_blank():
    assert server_proc.clean_line("\r\n") == ""


class _FakeProc:
    def __init__(self, raws):
        self.stdout = iter(raws)


def test_pump_collapses_one_bar_into_a_single_line(tmp_path):
    proc = server_proc.ServerProcess(tmp_path, 8765, on_log=lambda line: None)
    proc.proc = _FakeProc(["Loading weights:   0%|  | 0/398\n",
                           "Loading weights:  10%|# | 40/398\n",
                           "Loading weights: 100%|##| 398/398\n",
                           "done loading\n"])
    proc._pump()
    assert proc.lines == ["Loading weights: 100%|##| 398/398", "done loading"]


def test_pump_keeps_distinct_bars_apart(tmp_path):
    proc = server_proc.ServerProcess(tmp_path, 8765, on_log=lambda line: None)
    proc.proc = _FakeProc(["Loading pipeline components...:  20%|# | 1/5\n",
                           "Loading weights:  50%|##| 200/398\n",
                           "Loading weights: 100%|##| 398/398\n"])
    proc._pump()
    assert proc.lines == ["Loading pipeline components...:  20%|# | 1/5",
                          "Loading weights: 100%|##| 398/398"]


def test_venv_python_found(tmp_path):
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("", encoding="utf-8")
    assert server_proc.venv_python(tmp_path) == scripts / "python.exe"


def test_venv_python_missing(tmp_path):
    assert server_proc.venv_python(tmp_path) is None


def test_port_is_free_on_unused_port():
    assert server_proc.port_is_free(free_port())


def test_port_is_busy_while_bound():
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        assert not server_proc.port_is_free(held.getsockname()[1])


def test_pick_port_skips_busy():
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        busy = held.getsockname()[1]
        assert server_proc.pick_port(busy) != busy


def test_pick_port_keeps_free_one():
    port = free_port()
    assert server_proc.pick_port(port) == port


def test_probe_offline_when_nothing_listens():
    assert server_proc.probe(free_port(), timeout=0.5)["state"] == "offline"


@pytest.mark.parametrize("model,expected", [("ready", "ready"),
                                            ("loading", "loading")])
def test_probe_reads_pong(model, expected):
    holder = {}
    ready = threading.Event()

    async def handler(ws):
        async for _ in ws:
            await ws.send(json.dumps({"type": "pong", "model": model,
                                      "progress": 0.5,
                                      "stage": "text encoder"}))

    async def run():
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            holder["port"] = server.sockets[0].getsockname()[1]
            ready.set()
            await asyncio.Future()

    loop = asyncio.new_event_loop()
    threading.Thread(target=lambda: loop.run_until_complete(run()),
                     daemon=True).start()
    assert ready.wait(5)
    result = server_proc.probe(holder["port"], timeout=3.0)
    assert result["state"] == expected
    if expected == "loading":
        assert result["progress"] == 0.5
        assert result["stage"] == "text encoder"


def test_process_runs_and_collects_output(tmp_path):
    proc = server_proc.ServerProcess(tmp_path, 8765, on_log=lambda line: None)
    proc.python = sys.executable
    proc.args = ["-c", "print('hello from server')"]
    proc.start()
    proc.wait_for_exit(timeout=10)
    for _ in range(50):
        if proc.lines:
            break
        threading.Event().wait(0.05)
    assert any("hello from server" in line for line in proc.lines)
    assert not proc.is_alive()


def test_stop_kills_process(tmp_path):
    proc = server_proc.ServerProcess(tmp_path, 8765, on_log=lambda line: None)
    proc.python = sys.executable
    proc.args = ["-c", "import time; time.sleep(60)"]
    proc.start()
    assert proc.is_alive()
    proc.stop()
    assert not proc.is_alive()


def pid_alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True).stdout
    return str(pid) in out


# the server spawns workers of its own; stopping must take the whole tree
GRANDCHILD = (
    "import subprocess, sys, time\n"
    "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print(kid.pid, flush=True)\n"
    "time.sleep(120)\n"
)


def test_stop_kills_the_whole_tree(tmp_path):
    proc = server_proc.ServerProcess(tmp_path, 8765, on_log=lambda line: None)
    proc.python = sys.executable
    proc.args = ["-u", "-c", GRANDCHILD]
    proc.start()
    for _ in range(100):
        if proc.lines:
            break
        threading.Event().wait(0.1)
    grandchild = int(proc.lines[0].strip())
    assert pid_alive(grandchild)
    proc.stop()
    for _ in range(50):
        if not pid_alive(grandchild):
            break
        threading.Event().wait(0.1)
    assert not pid_alive(grandchild), "grandchild outlived the server"


# a launcher that is killed outright, the way Task Manager or a crash does it
FAKE_LAUNCHER = (
    "import sys, time\n"
    "sys.path.insert(0, {root!r})\n"
    "from launcher import server_proc\n"
    "p = server_proc.ServerProcess({root!r}, 8765, on_log=lambda line: None)\n"
    "p.python = sys.executable\n"
    "p.args = ['-c', 'import time; time.sleep(120)']\n"
    "p.start()\n"
    "print(p.proc.pid, flush=True)\n"
    "time.sleep(120)\n"
)


def test_hard_killed_launcher_takes_the_server_with_it(tmp_path):
    root = str(pathlib.Path(__file__).resolve().parent.parent.parent)
    launcher = subprocess.Popen(
        [sys.executable, "-u", "-c", FAKE_LAUNCHER.format(root=root)],
        stdout=subprocess.PIPE, text=True)
    server_pid = int(launcher.stdout.readline().strip())
    assert pid_alive(server_pid)
    # /F only, no /T: nothing walks the tree for us
    subprocess.run(["taskkill", "/F", "/PID", str(launcher.pid)],
                   capture_output=True)
    for _ in range(60):
        if not pid_alive(server_pid):
            break
        threading.Event().wait(0.1)
    assert not pid_alive(server_pid), "server outlived the killed launcher"


def test_start_is_idempotent(tmp_path):
    proc = server_proc.ServerProcess(tmp_path, 8765, on_log=lambda line: None)
    proc.python = sys.executable
    proc.args = ["-c", "import time; time.sleep(60)"]
    proc.start()
    first = proc.proc.pid
    proc.start()
    assert proc.proc.pid == first
    proc.stop()
