import asyncio
import json
import threading
import pytest
import websockets
from PIL import Image

import numpy as np
import server.main as srv
from server.protocol import image_to_b64, image_from_raw

HOST, PORT = "127.0.0.1", 8798


class FakeKlein:  # one model serves every mode, like the real one
    def txt2img(self, prompt, target_size, variants=4, on_progress=None):
        if on_progress:
            on_progress(0.5)
            on_progress(1.0)
        return [Image.new("RGBA", (1024, 1024), (255, 0, 0, 255))
                for _ in range(variants)]

    def edit_by_instruction(self, instruction, image, variants=4,
                            on_progress=None):
        return [image.resize((1024, 1024))] * variants

    def inpaint(self, prompt, image, mask, variants=4, on_progress=None):
        return [image.resize((1024, 1024))] * variants


@pytest.fixture()
def server_thread(monkeypatch):
    from server import models
    models.reset()
    models.register("klein", FakeKlein)
    monkeypatch.setattr(srv, "DEBUG_SAVE", False)  # keep output/ real-only
    loop = asyncio.new_event_loop()
    stop = loop.create_future()
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(srv.serve(HOST, PORT, stop, on_ready=ready.set))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(5)
    yield
    loop.call_soon_threadsafe(stop.set_result, None)
    t.join(timeout=5)
    models.reset()


def test_preload_loads_klein_before_any_request():
    import time
    from server import models
    models.reset()
    models.register("klein", FakeKlein)
    loop = asyncio.new_event_loop()
    stop = loop.create_future()
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(srv.serve(HOST, PORT + 1, stop,
                                          on_ready=ready.set, preload=True))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(5)
    try:
        deadline = time.time() + 5  # preload runs in the GPU worker thread
        while models._resident_name != "klein" and time.time() < deadline:
            time.sleep(0.01)
        assert models._resident_name == "klein"

        async def ping():
            async with websockets.connect(f"ws://{HOST}:{PORT + 1}") as ws:
                await ws.send(json.dumps({"type": "ping"}))
                return json.loads(await ws.recv())
        assert asyncio.run(ping())["model"] == "ready"
    finally:
        loop.call_soon_threadsafe(stop.set_result, None)
        t.join(timeout=5)
        models.reset()


def test_generate_returns_progress_then_result(server_thread):
    async def go():
        async with websockets.connect(f"ws://{HOST}:{PORT}",
                                      max_size=64 * 2**20) as ws:
            await ws.send(json.dumps({
                "id": "g1", "mode": "generate", "prompt": "sword",
                "target_size": [32, 32], "variants": 2, "frames": [],
            }))
            msgs = []
            while True:
                msg = json.loads(await ws.recv())
                msgs.append(msg)
                if msg["type"] in ("result", "error"):
                    return msgs
    msgs = asyncio.run(go())
    assert msgs[-1]["type"] == "result"
    assert len(msgs[-1]["images"]) == 2
    assert any(m["type"] == "progress" for m in msgs)
    values = [m["value"] for m in msgs if m["type"] == "progress"]
    assert 1.0 in values  # generation fills the bar to 100%
    stages = [m["stage"] for m in msgs
              if m["type"] == "progress" and m.get("stage")]
    assert "Decoding images" in stages       # emitted when the bar hits 100%
    assert "Post-processing 0/2" in stages
    assert "Post-processing 2/2" in stages


def test_edit_roundtrip(server_thread):
    src = Image.new("RGBA", (16, 16), (0, 200, 0, 255))
    async def go():
        async with websockets.connect(f"ws://{HOST}:{PORT}",
                                      max_size=64 * 2**20) as ws:
            await ws.send(json.dumps({
                "id": "e1", "mode": "edit", "prompt": "greener",
                "target_size": [16, 16], "variants": 1,
                "frames": [{"image": image_to_b64(src), "mask": None}],
            }))
            while True:
                msg = json.loads(await ws.recv())
                if msg["type"] in ("result", "error"):
                    return msg
    msg = asyncio.run(go())
    assert msg["type"] == "result" and len(msg["images"]) == 1


def test_instruct_roundtrip_with_stage(server_thread):
    src = Image.new("RGBA", (16, 16), (0, 100, 200, 255))
    async def go():
        async with websockets.connect(f"ws://{HOST}:{PORT}",
                                      max_size=64 * 2**20) as ws:
            await ws.send(json.dumps({
                "id": "i1", "mode": "instruct", "prompt": "front view",
                "target_size": [16, 16], "variants": 1,
                "frames": [{"image": image_to_b64(src), "mask": None}],
            }))
            msgs = []
            while True:
                msg = json.loads(await ws.recv())
                msgs.append(msg)
                if msg["type"] in ("result", "error"):
                    return msgs
    msgs = asyncio.run(go())
    assert msgs[-1]["type"] == "result"
    assert len(msgs[-1]["images"]) == 1
    stages = [m.get("stage") for m in msgs if m["type"] == "progress"]
    assert any(s and "Loading" in s for s in stages)


def test_generate_background_keep_stays_opaque(server_thread):
    """background=keep skips removal of the flat red fake image."""
    async def go():
        async with websockets.connect(f"ws://{HOST}:{PORT}",
                                      max_size=64 * 2**20) as ws:
            await ws.send(json.dumps({
                "id": "bk1", "mode": "generate", "prompt": "sword",
                "target_size": [8, 8], "variants": 1, "frames": [],
                "background": "keep",
            }))
            while True:
                msg = json.loads(await ws.recv())
                if msg["type"] in ("result", "error"):
                    return msg
    msg = asyncio.run(go())
    assert msg["type"] == "result"
    final = image_from_raw(msg["images"][0])
    assert (np.asarray(final)[:, :, 3] == 255).all()


def test_edit_crops_small_subject_to_fill_frame(server_thread):
    """Edit must crop to the subject so a small subject on a big canvas fills
    the target instead of collapsing to a few pixels."""
    from server import models

    class SmallSubjectKlein(FakeKlein):
        def edit_by_instruction(self, instruction, image, variants=4,
                                on_progress=None):
            canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 255))
            canvas.paste(Image.new("RGBA", (120, 120), (240, 240, 240, 255)),
                         (452, 800))
            return [canvas] * variants

    models.register("klein", SmallSubjectKlein)
    src = Image.new("RGBA", (16, 16), (240, 240, 240, 255))

    async def go():
        async with websockets.connect(f"ws://{HOST}:{PORT}",
                                      max_size=64 * 2**20) as ws:
            await ws.send(json.dumps({
                "id": "boat", "mode": "edit", "prompt": "make a boat",
                "target_size": [64, 64], "variants": 1, "strength": 0.9,
                "frames": [{"image": image_to_b64(src), "mask": None}],
            }))
            while True:
                msg = json.loads(await ws.recv())
                if msg["type"] in ("result", "error"):
                    return msg

    msg = asyncio.run(go())
    assert msg["type"] == "result"
    final = image_from_raw(msg["images"][0])
    opaque = int((np.asarray(final)[:, :, 3] > 0).sum())
    # Cropped fills most of 64x64; uncropped would be under 100 px of 4096.
    assert opaque > 1500, f"subject collapsed: only {opaque}/4096 opaque px"


def test_history_pages_newest_first(server_thread, monkeypatch, tmp_path):
    import json as _json
    for n, (stamp, prompt) in enumerate([("20260101-000000", "old sword"),
                                         ("20260102-000000", "new book")]):
        folder = tmp_path / f"{stamp}_generate_x_{n}"
        folder.mkdir()
        Image.new("RGBA", (8, 8), (n, 0, 0, 255)).save(folder / "final_0.png")
        (folder / "settings.json").write_text(_json.dumps(
            {"mode": "generate", "prompt": prompt}), encoding="utf-8")
    monkeypatch.setattr(srv, "DEBUG_DIR", tmp_path)

    async def go(payload):
        async with websockets.connect(f"ws://{HOST}:{PORT}",
                                      max_size=64 * 2**20) as ws:
            await ws.send(json.dumps(payload))
            return json.loads(await ws.recv())
    msg = asyncio.run(go({"type": "history", "offset": 0, "limit": 1}))
    assert msg["type"] == "history" and msg["total"] == 2
    assert msg["runs"][0]["prompt"] == "new book"  # newest first
    assert msg["runs"][0]["count"] == 1
    img = image_from_raw(msg["runs"][0]["images"][0])
    assert img.size == (8, 8)

    msg = asyncio.run(go({"type": "history", "offset": 0, "limit": 5,
                          "preview": True}))
    assert len(msg["runs"]) == 2
    assert all(len(r["images"]) == 1 for r in msg["runs"])


def test_t2i_size_matches_target_aspect():
    from server.instruct import t2i_size
    assert t2i_size((64, 64)) == (512, 512)
    assert t2i_size((64, 32)) == (512, 256)
    assert t2i_size((70, 70)) == (512, 512)  # rounds to /16
    w, h = t2i_size((128, 24))
    assert w == 512 and h % 16 == 0 and h >= 16
