import asyncio
import json
import logging
import threading
import pytest
import websockets
from server.main import serve, DropBenignHandshakeAborts

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
    assert msg["progress"] == 0.0     # no load in flight yet


def test_bad_request_returns_error(server_thread):
    async def go():
        async with websockets.connect(f"ws://{HOST}:{PORT}") as ws:
            await ws.send("{broken")
            return json.loads(await ws.recv())
    assert asyncio.run(go())["type"] == "error"


def _record_with_exc(exc):
    import sys
    try:
        raise exc
    except type(exc):
        return logging.LogRecord("websockets.server", logging.ERROR, __file__,
                                 1, "opening handshake failed", (),
                                 sys.exc_info())


def test_drop_benign_handshake_aborts_swallows_connection_closed():
    record = _record_with_exc(websockets.exceptions.ConnectionClosedError(None, None))
    assert DropBenignHandshakeAborts().filter(record) is False


def test_drop_benign_handshake_aborts_keeps_other_errors():
    record = _record_with_exc(ValueError("bad handshake"))
    assert DropBenignHandshakeAborts().filter(record) is True
