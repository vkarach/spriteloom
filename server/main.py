"""SpriteForge server: WebSocket endpoint on localhost."""
import asyncio
import functools
import json
import logging
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import websockets

from server import models
from server.postprocess import (crop_to_subject, fit_into, mirror_symmetry,
                                subject_palette, sprite_palette,
                                remove_background)
from server.protocol import ProtocolError, parse_request, error_msg, \
    progress_msg, result_msg

log = logging.getLogger("spriteforge")

_gpu_executor = ThreadPoolExecutor(max_workers=1)
DEBUG_DIR = pathlib.Path("output")
DEBUG_SAVE = True  # tests set this to False so fake runs don't litter output/
RAW_KEEP = 10  # runs whose full-size raw_*.png survive pruning


def _slug(text, max_words=4, max_len=32):
    words = [w for w in "".join(
        c if c.isalnum() or c.isspace() else " " for c in text).split()]
    return "-".join(words[:max_words])[:max_len].rstrip("-") or "no-prompt"


def _run_folders():
    """Run folders newest first; names sort by timestamp."""
    if not DEBUG_DIR.exists():
        return []
    return sorted((p for p in DEBUG_DIR.iterdir() if p.is_dir()),
                  key=lambda p: p.name, reverse=True)


def _prune_raw(keep=RAW_KEEP):
    """Raws are only useful for tuning postprocess; history shows finals."""
    for folder in _run_folders()[keep:]:
        for f in folder.glob("raw_*.png"):
            try:
                f.unlink()
            except OSError:
                log.debug("could not prune %s", f)


def _save_debug(req, raw_images, final_images, seeds=None):
    """One folder per request: raw originals, final sprites, settings."""
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
                "variants": req.variants,
                "target_size": list(req.target_size),
                "seeds": seeds or []}
        (folder / "settings.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8")
        _prune_raw()
    except OSError:
        log.exception("debug save failed")  # never break generation over this


def _history_msg(offset, limit, preview=False):
    """Page of past runs, newest first; preview=True sends one image each."""
    from PIL import Image
    from server.protocol import image_to_raw
    folders = _run_folders()
    runs = []
    for pos, folder in enumerate(folders[offset:offset + max(limit, 0)],
                                 start=offset):
        try:
            meta = json.loads((folder / "settings.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        files = sorted(folder.glob("final_*.png"))
        images = []
        for f in (files[:1] if preview else files):
            with Image.open(f) as im:  # else the handle lives until GC
                images.append(image_to_raw(im))
        if images:
            # absolute folder index: imageless runs are skipped, so a list
            # position is not a valid server offset
            runs.append({"name": folder.name, "offset": pos,
                         "mode": meta.get("mode", "?"),
                         "prompt": meta.get("prompt", ""),
                         "count": len(files),
                         "seeds": meta.get("seeds", []),
                         "images": images})
    return json.dumps({"type": "history", "total": len(folders),
                       "offset": offset, "runs": runs})


def _register_default_models():
    """Idempotent registration of the real pipeline."""
    def klein():
        from server.instruct import KleinPipeline
        p = KleinPipeline()
        p.load()
        return p

    models.register("klein", klein)


def _run(req, on_progress, on_stage):
    """Blocking generation + postprocess. Runs in the GPU worker thread."""
    def gen_progress(v):
        on_progress(v)
        if v >= 0.999:  # decode runs next, inside the pipe call
            on_stage("Decoding images")
    pipe = models.get("klein", on_stage=on_stage)
    seeds = pipe.variant_seeds(req.seed, req.variants)
    if req.mode in ("instruct", "edit"):
        # same Klein edit; they differ only in the panel UI
        raw = pipe.edit_by_instruction(req.prompt, req.frames[0].image,
                                       variants=req.variants,
                                       on_progress=gen_progress, seeds=seeds)
        palette_src = req.frames[0].image
    elif req.mode == "generate":
        on_stage("Preparing prompt")  # text encode runs before step ticks
        raw = pipe.txt2img(req.prompt, req.target_size,
                           variants=req.variants, on_progress=gen_progress,
                           seeds=seeds)
        palette_src = None
    else:  # inpaint - parse_request guarantees image+mask exist
        raw = pipe.inpaint(req.prompt, req.frames[0].image,
                           req.frames[0].mask, variants=req.variants,
                           on_progress=gen_progress, seeds=seeds)
        palette_src = req.frames[0].image

    # strip background at full res BEFORE shrinking: after the palette snap
    # the flood ate subject pixels that snapped near background colors
    pal = req.palette or (
        sprite_palette(palette_src) if palette_src is not None else None)
    out = []
    on_stage(f"Post-processing 0/{len(raw)}")
    for img in raw:
        if req.background == "keep":
            cut = img.convert("RGBA")
        else:
            cut = remove_background(img, tolerance=16,
                                    force=req.background == "remove")
        # Not inpaint: cropping would break its mask alignment.
        if req.mode in ("generate", "edit"):
            cut = crop_to_subject(cut)
        small = fit_into(cut, req.target_size,
                         palette=pal or subject_palette(cut, 16))
        if req.symmetry:
            small = mirror_symmetry(small)
        out.append(small)
        on_stage(f"Post-processing {len(out)}/{len(raw)}")
    _save_debug(req, raw, out, seeds)
    return out, seeds


class JobCancelled(Exception):
    """Client disconnected mid-generation; abort the GPU job."""


async def handle_request(ws, req):
    loop = asyncio.get_running_loop()

    def send(msg):
        # runs in the GPU worker thread; a closed socket means Cancel
        if getattr(ws, "close_code", None) is not None:
            raise JobCancelled()
        f = asyncio.run_coroutine_threadsafe(ws.send(msg), loop)
        # blocking hands the GIL to the loop: measured up to 2x faster
        # delivery than fire-and-forget (ordering holds either way)
        try:
            f.result(5)
        except Exception as e:
            log.debug("progress send failed: %r", e)

    def on_progress(v):
        send(progress_msg(req.id, v))

    def on_stage(text):
        send(progress_msg(req.id, 0.0, stage=text))

    images, seeds = await loop.run_in_executor(
        _gpu_executor, functools.partial(_run, req, on_progress, on_stage))
    await ws.send(result_msg(req.id, images, seeds))


async def _handler(ws):
    try:
        await _serve_connection(ws)
    except websockets.exceptions.ConnectionClosed:
        # the plugin opens a fresh socket per ping/request and drops it without
        # a close handshake; that is normal, not a handler failure to log
        log.debug("client closed connection without a close frame")


async def _serve_connection(ws):
    async for message in ws:
        try:
            data = json.loads(message)
            if isinstance(data, dict) and data.get("type") == "ping":
                # the panel shows a load bar until Klein is resident
                model = "ready" if models.is_ready("klein") else "loading"
                pong = {"type": "pong", "model": model}
                if model == "loading":
                    prog = models.load_progress()
                    pong["progress"] = round(prog[0], 3) if prog else 0.0
                    if prog and prog[1]:
                        pong["stage"] = prog[1]
                await ws.send(json.dumps(pong))
                continue
            if isinstance(data, dict) and data.get("type") == "history":
                await ws.send(_history_msg(int(data.get("offset", 0)),
                                           int(data.get("limit", 1)),
                                           bool(data.get("preview"))))
                continue
        except (json.JSONDecodeError, ValueError, TypeError):
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
        except websockets.exceptions.ConnectionClosed:
            raise  # client vanished mid-request; _handler logs it at debug
        except Exception as e:  # never die silently
            log.exception("request failed")
            try:
                await ws.send(error_msg(req.id, f"{type(e).__name__}: {e}"))
            except Exception:
                log.debug("client gone before error could be delivered")


def _preload_klein():
    """Warm the model at startup; a request arriving mid-load queues behind."""
    def done(f):
        if f.exception():
            log.error("klein preload failed: %r", f.exception())
        else:
            log.info("klein preloaded, ready for prompts")
    _gpu_executor.submit(models.get, "klein").add_done_callback(done)


async def serve(host="127.0.0.1", port=8765, stop=None, on_ready=None,
                preload=False):
    if not models._factories:
        _register_default_models()
    # GIL-heavy worker; a short switch interval keeps pings answered
    sys.setswitchinterval(0.001)
    if preload:
        _preload_klein()
    # no keepalive: the 20s ping timeout killed sockets mid-job
    async with websockets.serve(_handler, host, port, max_size=64 * 2**20,
                                ping_interval=None):
        log.info("SpriteForge server on ws://%s:%s", host, port)
        if on_ready:
            on_ready()
        await (stop if stop is not None else asyncio.Future())


if __name__ == "__main__":
    from server.config import HOST, load_port
    logging.basicConfig(level=logging.INFO)
    # The panel pings every 10s; websockets logs every open/close at INFO.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    try:
        asyncio.run(serve(host=HOST, port=load_port(), preload=True))
    except KeyboardInterrupt:
        log.info("SpriteForge server stopped. Bye.")
