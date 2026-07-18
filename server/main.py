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
                                subject_palette, sprite_palette,
                                remove_background)
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


def _history_msg(offset, limit, preview=False):
    """Page of past runs, newest first: folder names sort by timestamp.
    preview=True sends only the first image of each run (list thumbnails)."""
    from PIL import Image
    from server.protocol import image_to_raw
    folders = sorted((p for p in DEBUG_DIR.iterdir() if p.is_dir()),
                     key=lambda p: p.name, reverse=True) \
        if DEBUG_DIR.exists() else []
    runs = []
    for pos, folder in enumerate(folders[offset:offset + max(limit, 0)],
                                 start=offset):
        try:
            meta = json.loads((folder / "settings.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        files = sorted(folder.glob("final_*.png"))
        images = [image_to_raw(Image.open(f))
                  for f in (files[:1] if preview else files)]
        if images:
            # offset = absolute folder index: runs without images are
            # skipped, so a list position is not a valid server offset
            runs.append({"name": folder.name, "offset": pos,
                         "mode": meta.get("mode", "?"),
                         "prompt": meta.get("prompt", ""),
                         "count": len(files),
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
    """Blocking generation + postprocess. Runs in the GPU worker thread.

    The bar fills 0..1 over the diffusion steps; VAE decode and postprocess
    run after that and are reported as stage labels, not bar movement.
    """
    def gen_progress(v):
        on_progress(v)
        if v >= 0.999:  # last step done; decode runs next inside the pipe call
            on_stage("Decoding images")
    if req.mode in ("instruct", "edit"):
        # Both are instruction edits on Klein; they differ only in the panel
        # UI (view presets vs a free prompt).
        pipe = models.get("klein", on_stage=on_stage)
        raw = pipe.edit_by_instruction(req.prompt, req.frames[0].image,
                                       variants=req.variants,
                                       on_progress=gen_progress)
        palette_src = req.frames[0].image
    elif req.mode == "generate":
        pipe = models.get("klein", on_stage=on_stage)
        on_stage("Preparing prompt")  # text encode runs before step ticks
        raw = pipe.txt2img(req.prompt, req.target_size,
                           variants=req.variants, on_progress=gen_progress)
        palette_src = None
    else:  # inpaint — parse_request guarantees image+mask exist
        pipe = models.get("klein", on_stage=on_stage)
        raw = pipe.inpaint(req.prompt, req.frames[0].image,
                           req.frames[0].mask, variants=req.variants,
                           on_progress=gen_progress)
        palette_src = req.frames[0].image

    # Order matters: strip the background at full resolution first (high
    # contrast, thick outlines - flood fill is reliable there), THEN shrink.
    # Doing it after the palette snap let the flood eat subject pixels that
    # snapped to near-background colors.
    pal = sprite_palette(palette_src) if palette_src is not None else None
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
    _save_debug(req, raw, out)
    return out


class JobCancelled(Exception):
    """Client disconnected mid-generation; abort the GPU job."""


async def handle_request(ws, req):
    loop = asyncio.get_running_loop()

    def send(msg):
        # Called from the GPU worker thread.  If the client is gone (Cancel
        # closes the socket), abort the job instead of burning GPU time.
        if getattr(ws, "close_code", None) is not None:
            raise JobCancelled()
        f = asyncio.run_coroutine_threadsafe(ws.send(msg), loop)
        # Wait for the actual send: the CPU-offload pipeline hogs the GIL so
        # hard the loop otherwise flushes progress in per-variant bursts.
        # Waiting also guarantees ordering before the final result message.
        try:
            f.result(5)
        except Exception as e:
            log.debug("progress send failed: %r", e)

    def on_progress(v):
        send(progress_msg(req.id, v))

    def on_stage(text):
        send(progress_msg(req.id, 0.0, stage=text))

    images = await loop.run_in_executor(
        _gpu_executor, functools.partial(_run, req, on_progress, on_stage))
    await ws.send(result_msg(req.id, images))


async def _handler(ws):
    async for message in ws:
        try:
            data = json.loads(message)
            if isinstance(data, dict) and data.get("type") == "ping":
                # the panel shows "loading" until Klein is resident
                model = "ready" if models.is_ready("klein") else "loading"
                await ws.send(json.dumps({"type": "pong", "model": model}))
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
        except Exception as e:  # never die silently
            log.exception("request failed")
            try:
                await ws.send(error_msg(req.id, f"{type(e).__name__}: {e}"))
            except Exception:
                log.debug("client gone before error could be delivered")


def _preload_klein():
    """Warm the model in the GPU worker at startup instead of on the first
    prompt. A request arriving mid-load queues behind it, same as before."""
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
    if preload:
        _preload_klein()
    # no keepalive pings: GIL-heavy generation starves the loop and the 20s
    # ping timeout used to kill the socket mid-job (localhost: TCP close is
    # enough to detect a dead client)
    async with websockets.serve(_handler, host, port, max_size=64 * 2**20,
                                ping_interval=None):
        log.info("SpriteForge server on ws://%s:%s", host, port)
        if on_ready:
            on_ready()
        await (stop if stop is not None else asyncio.Future())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # The panel pings every 10s; websockets logs every open/close at INFO.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    try:
        asyncio.run(serve(preload=True))
    except KeyboardInterrupt:
        log.info("SpriteForge server stopped. Bye.")
