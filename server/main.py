"""SpriteForge server: WebSocket endpoint on localhost."""
import asyncio
import functools
import json
import logging
import pathlib
import time
from concurrent.futures import ThreadPoolExecutor

import websockets

from server import models
from server.postprocess import (crop_to_subject, fit_into, mirror_symmetry,
                                snap_to_palette, subject_palette,
                                sprite_palette, remove_background)
from server.protocol import ProtocolError, parse_request, error_msg, \
    progress_msg, result_msg

log = logging.getLogger("spriteforge")

_gpu_executor = ThreadPoolExecutor(max_workers=1)
DEBUG_DIR = pathlib.Path("output")
DEBUG_SAVE = True  # tests set this to False so fake runs don't litter output/


def _slug(text, max_words=4, max_len=32):
    words = [w for w in "".join(
        c if c.isalnum() or c.isspace() else " " for c in text).split()]
    return "-".join(words[:max_words])[:max_len].rstrip("-") or "no-prompt"


def _save_debug(req, raw_images, final_images):
    """Keep every request in its own folder: uncompressed originals, final
    sprites, and the settings that produced them."""
    if not DEBUG_SAVE:
        return
    try:
        safe_id = "".join(c if c.isalnum() or c == "-" else "_" for c in req.id)
        folder = DEBUG_DIR / (f"{time.strftime('%Y%m%d-%H%M%S')}_{req.mode}_"
                              f"{_slug(req.prompt)}_{safe_id}")
        folder.mkdir(parents=True, exist_ok=True)
        for n, img in enumerate(raw_images):
            img.save(folder / f"raw_{n}.png")
        for n, img in enumerate(final_images):
            img.save(folder / f"final_{n}.png")
        meta = {"mode": req.mode, "prompt": req.prompt,
                "variants": req.variants, "strength": req.strength,
                "target_size": list(req.target_size)}
        (folder / "settings.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        log.exception("debug save failed")  # never break generation over this


def _register_default_models():
    """Idempotent registration of the real pipelines."""
    def sdxl():
        from server.pipeline import Pipeline
        p = Pipeline()
        p.load()
        return p

    def klein():
        from server.instruct import InstructPipeline
        p = InstructPipeline()
        p.load()
        return p

    models.register("sdxl", sdxl)
    models.register("klein", klein)


def _run(req, on_progress, on_stage):
    """Blocking generation + postprocess. Runs in the GPU worker thread.

    Progress honesty: diffusion steps only cover 0..0.85 of the bar; VAE
    decode happens inside the pipeline call after the last step, so 0.85
    is where the bar waits for it.  0.95 = decoding done, postprocessing.
    """
    step_progress = lambda v: on_progress(v * 0.85)
    if req.mode == "instruct":
        pipe = models.get("klein", on_stage=on_stage)
        raw = pipe.edit_by_instruction(req.prompt, req.frames[0].image,
                                       variants=req.variants,
                                       on_progress=step_progress)
        palette_src = req.frames[0].image
    else:
        pipe = models.get("sdxl", on_stage=on_stage)
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
    # Order matters: strip the background at full resolution first (high
    # contrast, thick outlines - flood fill is reliable there), THEN shrink.
    # Doing it after the palette snap let the flood eat subject pixels that
    # snapped to near-background colors.
    pal = sprite_palette(palette_src) if palette_src is not None else None
    out = []
    for img in raw:
        cut = remove_background(img, tolerance=16)
        if req.mode == "generate":
            cut = crop_to_subject(cut)
        small = fit_into(cut, req.target_size)
        small = snap_to_palette(small, pal or subject_palette(cut, 16))
        if req.symmetry:
            small = mirror_symmetry(small)
        out.append(small)
    _save_debug(req, raw, out)
    return out


class JobCancelled(Exception):
    """Client disconnected mid-generation; abort the GPU job."""


async def handle_request(ws, req):
    loop = asyncio.get_running_loop()
    pending = []

    def on_progress(v):
        # Called from the GPU worker thread on every diffusion step.  If the
        # client is gone (Cancel closes the socket), abort the job instead of
        # burning GPU time on a result nobody will receive.
        if getattr(ws, "close_code", None) is not None:
            raise JobCancelled()
        f = asyncio.run_coroutine_threadsafe(
            ws.send(progress_msg(req.id, v)), loop)
        pending.append(f)

    def on_stage(text):
        if getattr(ws, "close_code", None) is not None:
            raise JobCancelled()
        f = asyncio.run_coroutine_threadsafe(
            ws.send(progress_msg(req.id, 0.0, stage=text)), loop)
        pending.append(f)

    images = await loop.run_in_executor(
        _gpu_executor, functools.partial(_run, req, on_progress, on_stage))

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
        except JobCancelled:
            log.info("request %s cancelled by client", req.id)
            break  # socket is already closed; stop serving this connection
        except Exception as e:  # never die silently
            log.exception("request failed")
            try:
                await ws.send(error_msg(req.id, f"{type(e).__name__}: {e}"))
            except Exception:
                log.debug("client gone before error could be delivered")


async def serve(host="127.0.0.1", port=8765, stop=None, on_ready=None):
    if not models._factories:
        _register_default_models()
    async with websockets.serve(_handler, host, port, max_size=64 * 2**20):
        log.info("SpriteForge server on ws://%s:%s", host, port)
        if on_ready:
            on_ready()
        await (stop if stop is not None else asyncio.Future())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        log.info("SpriteForge server stopped. Bye.")
