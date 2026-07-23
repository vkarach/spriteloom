"""FLUX.2 Klein 4B: text-to-image generation and instruction-based editing.

One resident pipeline (~11.5 GB VRAM): 8-bit text encoder + bf16
transformer. Quantizing the TRANSFORMER breaks t2i (pure noise); the text
encoder takes 8-bit fine, and the bf16 transformer also edits faster than
the 8-bit one did.
"""
import contextlib
import logging
import pathlib
import random
from collections.abc import ItemsView

from PIL import Image

MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"
STYLE_SUFFIX = ". Keep the same pixel art style, colors and character design."
STEPS = 8          # Klein is step-distilled; more steps drift toward realism
GUIDANCE = 1.0
T2I_SUFFIX = (" Flat 2D pixel art game sprite, crisp pixels, flat colors,"
              " clean outlines, single centered object on a plain solid"
              " background.")
# 512px is plenty for pixel art; 1024px batches overflow 16 GB and WDDM
# starts paging VRAM through system RAM (observed: 10x+ slowdown).
MAX_SIDE = 512

# bf16 peaks ~13 GB; only 12+ GB cards get it. Below that, offload is the
# lossless default (fp8 stays behind an explicit override until verified).
BF16_MIN_FREE = 11 * 1024 ** 3

# reading the files is the measured part of the load; the rest is the GPU copy
READ_SPAN = 0.9

log = logging.getLogger("spriteloom.instruct")


def resolve_mode(mode: str, free_bytes: int) -> str:
    """Concrete load mode. Explicit modes pass through; 'auto' picks by VRAM."""
    if mode != "auto":
        return mode
    return "bf16" if free_bytes >= BF16_MIN_FREE else "offload"


# Image tokens double the edit sequence, so edit chunks are half t2i.
_CHUNKS = {"bf16": {"t2i": 4, "edit": 2}}
_LOW_VRAM_CHUNKS = {"t2i": 2, "edit": 1}


def chunk_size(mode: str, kind: str) -> int:
    """Images per pipe call for a resolved mode; smaller on 8 GB paths."""
    return _CHUNKS.get(mode, _LOW_VRAM_CHUNKS)[kind]


def t2i_size(target_size: tuple[int, int],
             max_side: int = MAX_SIDE) -> tuple[int, int]:
    """Generation size matching the target aspect: long side ~max_side,
    both dims multiples of 16."""
    w, h = target_size
    scale = max_side / max(w, h)
    return (max(16, round(w * scale / 16) * 16),
            max(16, round(h * scale / 16) * 16))


def component_bytes(models_dir: str) -> dict:
    """On-disk size of each pipeline component in the newest snapshot."""
    root = (pathlib.Path(models_dir)
            / ("models--" + MODEL_ID.replace("/", "--")) / "snapshots")
    snaps = sorted((p for p in root.glob("*") if p.is_dir()),
                   key=lambda p: p.stat().st_mtime)
    if not snaps:
        return {}
    return {d.name: sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            for d in snaps[-1].iterdir() if d.is_dir()}


def _cum_weights(bar, weigh):
    """Cumulative 0..1 slices of a bar by real weight, None to split evenly."""
    total = getattr(bar, "total", None) or 0
    items = getattr(bar, "iterable", None)
    if not weigh or total <= 0:
        return None
    # re-iterable only: consuming a one-shot iterator would eat the load
    if not isinstance(items, (ItemsView, list, tuple)):
        return None
    names = [i[0] if isinstance(i, tuple) else i for i in items]
    if len(names) != total:
        return None
    weights = weigh(names)
    grand = sum(weights)
    if grand <= 0:
        return None
    cum, run = [0.0], 0.0
    for w in weights:
        run += w / grand
        cum.append(run)
    return cum


@contextlib.contextmanager
def _report_tqdm(report, weigh=None):
    """Relay the nested tqdm bars of from_pretrained as one (fraction, label,
    ceiling), each bar filling its parent's current unit, units sized by
    weigh(names). diffusers resolves tqdm at call time, so swapping it."""
    import tqdm as tqdm_lib
    import tqdm.auto as tqdm_auto
    real = tqdm_lib.tqdm
    stack = []  # active bars, outermost first
    floor = [None, 0.0]  # outermost bar the floor belongs to, value

    def current():
        frac, span, label = 0.0, 1.0, None
        for bar in stack:
            total = getattr(bar, "total", None) or 0
            if total <= 0:
                continue
            seen = getattr(bar, "_seen", None)
            done = min(max(bar.n if seen is None else seen, 0), total)
            cum = getattr(bar, "_cum", None)
            if cum:
                start = cum[done]
                unit = cum[min(done + 1, total)] - start
            else:
                start = done / total
                unit = 1.0 / total if done < total else 0.0
            frac += span * start
            span *= unit
            label = (getattr(bar, "desc", "") or "").rstrip(": ") or label
        return min(frac, 1.0), label, min(frac + span, 1.0)

    def emit():
        if not stack:
            return
        frac, label, ceiling = current()
        # a fresh outermost bar is a new 0..1 run, not a step back
        if stack[0] is not floor[0]:
            floor[:] = [stack[0], 0.0]
        frac = max(frac, floor[1])
        floor[1] = frac
        report(frac, label, max(ceiling, frac))

    class Mirror(real):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._cum = _cum_weights(self, weigh)
            self._seen = None
            stack.append(self)
            emit()

        def __iter__(self):
            # tqdm keeps the count local and syncs self.n only on redraw
            self._seen = 0
            for item in super().__iter__():
                emit()
                yield item
                self._seen += 1

        def update(self, n=1):
            out = super().update(n)
            emit()
            return out

        def close(self):
            # tqdm closes an iterated bar itself, so fill its span here
            if self in stack:
                if self._seen is not None:
                    self._seen = getattr(self, "total", None) or self._seen
                emit()
                stack.remove(self)
            super().close()

    tqdm_lib.tqdm = Mirror
    tqdm_auto.tqdm = Mirror
    try:
        yield
    finally:
        tqdm_lib.tqdm = real
        tqdm_auto.tqdm = real


class KleinPipeline:
    def __init__(self, models_dir: str = "models", mode: str = "auto"):
        self.models_dir = models_dir
        self.mode = mode
        self._resolved = None
        self._pipe = None

    def load(self):
        if self._pipe is not None:
            return
        from server import models
        models.set_load_progress(0.0, "Importing torch")
        import gc
        import torch
        from diffusers import Flux2KleinPipeline
        free = torch.cuda.mem_get_info()[0] if torch.cuda.is_available() else 0
        self._resolved = resolve_mode(self.mode, free)
        log.info("loading %s (mode %s -> %s; first run downloads ~15 GB)...",
                 MODEL_ID, self.mode, self._resolved)
        models.set_load_progress(0.0, "Reading model files")
        builders = {"offload": self._build_offload, "fp8": self._build_fp8}
        build = builders.get(self._resolved, self._build_bf16)
        # two 7 GB components carry the wait; equal steps would misplace it
        sizes = component_bytes(self.models_dir)
        weigh = lambda names: [sizes.get(n, 0) for n in names]
        def report(v, stage=None, ceiling=None):
            models.set_load_progress(
                v * READ_SPAN, stage,
                ceiling=None if ceiling is None else ceiling * READ_SPAN)
        with _report_tqdm(report, weigh=weigh):
            self._pipe = build(torch, Flux2KleinPipeline)
        models.set_load_progress(1.0, "Finishing up")
        self._pipe.vae.enable_slicing()
        gc.collect()
        torch.cuda.empty_cache()
        log.info("klein pipeline ready (%s)", self._resolved)

    def _to_cuda(self, pipe):
        from server import models
        models.set_load_progress(READ_SPAN, "Moving model to GPU",
                                 ceiling=0.99)
        return pipe.to("cuda")

    def _build_bf16(self, torch, Pipe):
        """12+ GB resident: 8-bit text encoder + bf16 transformer."""
        try:
            from diffusers import PipelineQuantizationConfig
            quant = PipelineQuantizationConfig(
                quant_backend="bitsandbytes_8bit",
                quant_kwargs={"load_in_8bit": True},
                components_to_quantize=["text_encoder"],
            )
            pipe = Pipe.from_pretrained(
                MODEL_ID, torch_dtype=torch.bfloat16,
                cache_dir=self.models_dir,
                quantization_config=quant)
            return self._to_cuda(pipe)
        except Exception:
            log.exception("resident load failed; falling back to CPU "
                          "offload (slower, ~16 GB of system RAM)")
            pipe = Pipe.from_pretrained(
                MODEL_ID, torch_dtype=torch.bfloat16,
                cache_dir=self.models_dir)
            pipe.enable_model_cpu_offload()
            return pipe

    def _build_fp8(self, torch, Pipe):
        """8 GB resident: float8 weight-only transformer + 8-bit text encoder.
        Near-lossless on Ada/Blackwell FP8 cores; unverified on this model."""
        from diffusers import PipelineQuantizationConfig
        from diffusers.quantizers.quantization_config import TorchAoConfig
        from torchao.quantization import Float8WeightOnlyConfig
        from transformers import BitsAndBytesConfig
        quant = PipelineQuantizationConfig(quant_mapping={
            "transformer": TorchAoConfig(Float8WeightOnlyConfig()),
            "text_encoder": BitsAndBytesConfig(load_in_8bit=True),
        })
        pipe = Pipe.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16,
            cache_dir=self.models_dir,
            quantization_config=quant)
        return self._to_cuda(pipe)

    def _build_offload(self, torch, Pipe):
        """8 GB lossless: bf16 weights streamed module-by-module off the GPU.
        Slower but keeps output identical to bf16."""
        pipe = Pipe.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16,
            cache_dir=self.models_dir)
        pipe.enable_sequential_cpu_offload()
        return pipe

    @staticmethod
    def variant_seeds(seed, variants):
        """One seed per variant so a single good result can be reproduced."""
        if seed is None:
            return [random.randrange(2**32) for _ in range(variants)]
        return [(seed + i) % 2**32 for i in range(variants)]

    @staticmethod
    def _generators(seeds):
        import torch
        return [torch.Generator(device="cuda").manual_seed(s) for s in seeds]

    def _cb(self, on_progress, done, chunk, total):
        if on_progress is None:
            return None
        def cb(pipe, step, timestep, kw):
            frac = (done + chunk * (step + 1) / STEPS) / total
            on_progress(min(1.0, frac))
            return kw
        return cb

    @staticmethod
    def _prep_input(image):
        """Integer-factor upscale + pad to model-friendly dims; a fractional
        upscale shows stretched pixels and comes back off-grid and wobbly."""
        rgb = image.convert("RGB")
        w, h = rgb.size
        k = max(1, MAX_SIDE // max(w, h))
        big = rgb.resize((w * k, h * k), Image.NEAREST)
        pw = -(-big.width // 16) * 16
        ph = -(-big.height // 16) * 16
        canvas = Image.new("RGB", (pw, ph), (0, 0, 0))
        canvas.paste(big, (0, 0))
        return canvas, (big.width, big.height)

    def edit_by_instruction(self, instruction, image, variants=4,
                            on_progress=None, seeds=None):
        self.load()
        big, (bw, bh) = self._prep_input(image)
        seeds = seeds or self.variant_seeds(None, variants)
        out = []
        cap = chunk_size(self._resolved, "edit")
        while len(out) < variants:
            chunk = min(cap, variants - len(out))
            imgs = self._pipe(
                prompt=instruction + STYLE_SUFFIX,
                image=big,
                width=big.size[0], height=big.size[1],
                guidance_scale=GUIDANCE,
                num_inference_steps=STEPS,
                num_images_per_prompt=chunk,
                generator=self._generators(seeds[len(out):len(out) + chunk]),
                callback_on_step_end=self._cb(on_progress, len(out), chunk,
                                              variants),
            ).images
            out.extend(img.crop((0, 0, bw, bh)) for img in imgs)
        return [i.convert("RGBA") for i in out]

    def inpaint(self, prompt, image, mask, variants=4, on_progress=None,
                seeds=None):
        """Klein edits the whole frame; only the masked region is kept."""
        edits = self.edit_by_instruction(prompt, image, variants=variants,
                                         on_progress=on_progress, seeds=seeds)
        big_src = image.convert("RGBA").resize(edits[0].size, Image.NEAREST)
        big_mask = mask.convert("L").resize(edits[0].size, Image.NEAREST)
        out = []
        for e in edits:
            comp = big_src.copy()
            comp.paste(e, (0, 0), big_mask)
            out.append(comp)
        return out

    def txt2img(self, prompt, target_size, variants=4, on_progress=None,
                seeds=None):
        self.load()
        w, h = t2i_size(target_size)
        seeds = seeds or self.variant_seeds(None, variants)
        out = []
        cap = chunk_size(self._resolved, "t2i")
        while len(out) < variants:
            chunk = min(cap, variants - len(out))
            out += self._pipe(
                prompt=prompt + T2I_SUFFIX,
                width=w, height=h,
                num_inference_steps=STEPS,
                num_images_per_prompt=chunk,
                generator=self._generators(seeds[len(out):len(out) + chunk]),
                callback_on_step_end=self._cb(on_progress, len(out), chunk,
                                              variants),
            ).images
        return [i.convert("RGBA") for i in out]
