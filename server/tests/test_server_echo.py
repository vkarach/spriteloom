import asyncio
import json
import threading
import pytest
import websockets
from server.main import serve

HOST, PORT = "127.0.0.1", 8799  # test port, not the real 8765


@pytest.fixture()
def server_thread():
    loop = asyncio.new_event_loop()
    stop = loop.create_future()
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve(HOST, PORT, stop, on_ready=ready.set))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(5)
    yield
    loop.call_soon_threadsafe(stop.set_result, None)
    t.join(timeout=5)


def test_ping_pong(server_thread):
    async def go():
        async with websockets.connect(f"ws://{HOST}:{PORT}") as ws:
            await ws.send(json.dumps({"type": "ping"}))
            return json.loads(await ws.recv())
    msg = asyncio.run(go())
    assert msg["type"] == "pong"
    assert msg["model"] == "loading"  # nothing resident in this fixture


def test_bad_request_returns_error(server_thread):
    async def go():
        async with websockets.connect(f"ws://{HOST}:{PORT}") as ws:
            await ws.send("{broken")
            return json.loads(await ws.recv())
    assert asyncio.run(go())["type"] == "error"
