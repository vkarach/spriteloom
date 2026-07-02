"""SpriteForge server: WebSocket endpoint on localhost."""
import asyncio
import functools
import json
import logging
import pathlib
import time
from concurrent.futures import ThreadPoolExecutor

import websockets

from server.postprocess import (downscale, extract_palette, snap_to_palette,
                                sprite_palette, remove_background)
from server.protocol import ProtocolError, parse_request, error_msg, progress_msg, result_msg

log = logging.getLogger("spriteforge")

_gpu_executor = ThreadPoolExecutor(max_workers=1)
DEBUG_DIR = pathlib.Path("output")


def _save_debug(req, raw_images):
    """Keep every request's uncompressed originals + settings for debugging."""
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        safe_id = "".join(c if c.isalnum() or c == "-" else "_" for c in req.id)
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}_{req.mode}_{safe_id}"
        for n, img in enumerate(raw_images):
            img.save(DEBUG_DIR / f"{stem}_{n}_raw.png")
        meta = {"mode": req.mode, "prompt": req.prompt,
                "variants": req.variants, "strength": req.strength,
                "target_size": list(req.target_size)}
        (DEBUG_DIR / f"{stem}.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        log.exception("debug save failed")  # never break generation over this


def _default_pipeline_factory():
    from server.pipeline import Pipeline
    return Pipeline()


PIPELINE_FACTORY = _default_pipeline_factory
_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        _pipeline = PIPELINE_FACTORY()
    return _pipeline


def _run(req, on_progress):
    """Blocking generation + postprocess. Runs in a worker thread.

    Progress honesty: diffusion steps only cover 0..0.85 of the bar; VAE
    decode happens inside the pipeline call after the last step, so 0.85
    is where the bar waits for it.  0.95 = decoding done, postprocessing.
    """
    pipe = _get_pipeline()
    step_progress = lambda v: on_progress(v * 0.85)
    if req.mode == "generate":
        raw = pipe.txt2img(req.prompt, variants=req.variants,
                           on_progress=step_progress)
        palette_src = None
    elif req.mode == "edit":
        raw = pipe.img2img(req.prompt, req.frames[0].image,
                           strength=req.strength, variants=req.variants,
                           on_progress=step_progress)
        palette_src = req.frames[0].image
    else:  # inpaint — parse_request guarantees image+mask exist
        raw = pipe.inpaint(req.prompt, req.frames[0].image,
                           req.frames[0].mask, variants=req.variants,
                           on_progress=step_progress)
        palette_src = req.frames[0].image

    on_progress(0.95)
    _save_debug(req, raw)
    pal = sprite_palette(palette_src) if palette_src is not None else None
    out = []
    for img in raw:
        small = downscale(img, req.target_size)
        small = snap_to_palette(small, pal or extract_palette(img, 16))
        small = remove_background(small)
        out.append(small)
    return out


async def handle_request(ws, req):
    loop = asyncio.get_running_loop()
    pending = []

    def on_progress(v):
        f = asyncio.run_coroutine_threadsafe(
            ws.send(progress_msg(req.id, v)), loop)
        pending.append(f)

    images = await loop.run_in_executor(
        _gpu_executor, functools.partial(_run, req, on_progress))

    # Drain all in-flight progress sends before the result so ordering is
    # guaranteed.  return_exceptions=True ensures a closed socket during a
    # progress send doesn't abort the result path.
    results = await asyncio.gather(
        *[asyncio.wrap_future(f) for f in pending],
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            log.debug("progress send failed: %r", r)

    await ws.send(result_msg(req.id, images))


async def _handler(ws):
    async for message in ws:
        try:
            data = json.loads(message)
            if isinstance(data, dict) and data.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue
        except json.JSONDecodeError:
            pass  # fall through to parse_request for a proper error
        try:
            req = parse_request(message)
        except ProtocolError as e:
            await ws.send(error_msg("", str(e)))
            continue
        try:
            await handle_request(ws, req)
        except Exception as e:  # never die silently
            log.exception("request failed")
            await ws.send(error_msg(req.id, f"{type(e).__name__}: {e}"))


async def serve(host="127.0.0.1", port=8765, stop=None, on_ready=None):
    async with websockets.serve(_handler, host, port, max_size=64 * 2**20):
        log.info("SpriteForge server on ws://%s:%s", host, port)
        if on_ready:
            on_ready()
        await (stop if stop is not None else asyncio.Future())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())
