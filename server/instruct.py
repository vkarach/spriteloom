"""FLUX.2 Klein 4B: text-to-image generation and instruction-based editing.

One resident pipeline (~11.5 GB VRAM): 8-bit text encoder + bf16
transformer. Quantizing the TRANSFORMER breaks t2i (pure noise); the text
encoder takes 8-bit fine, and the bf16 transformer also edits faster than
the 8-bit one did.
"""
import contextlib
import logging
import random

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


@contextlib.contextmanager
def _report_tqdm(report):
    """Relay the CURRENT tqdm bar of from_pretrained (innermost one with a
    total) as (fraction, description) - the panel shows the same stages the
    console does instead of one merged made-up percentage.
    diffusers/transformers resolve tqdm at call time, so swapping the class
    catches their bars."""
    import tqdm as tqdm_lib
    import tqdm.auto as tqdm_auto
    real = tqdm_lib.tqdm
    stack = []  # active bars, outermost first

    def current():
        for bar in reversed(stack):
            t = getattr(bar, "total", None) or 0
            if t > 0:
                label = (getattr(bar, "desc", "") or "").rstrip(": ")
                return min(bar.n / t, 1.0), (label or None)
        return 0.0, None

    class Mirror(real):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            stack.append(self)
            report(*current())

        def update(self, n=1):
            out = super().update(n)
            report(*current())
            return out

        def close(self):
            if self in stack:
                stack.remove(self)
            super().close()
            if stack:
                report(*current())

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
        with _report_tqdm(models.set_load_progress):
            self._pipe = build(torch, Flux2KleinPipeline)
        models.set_load_progress(1.0, "Finishing up")
        self._pipe.vae.enable_slicing()
        gc.collect()
        torch.cuda.empty_cache()
        log.info("klein pipeline ready (%s)", self._resolved)

    def _build_bf16(self, torch, Pipe):
        """12+ GB resident: 8-bit text encoder + bf16 transformer."""
        try:
            from diffusers import PipelineQuantizationConfig
            quant = PipelineQuantizationConfig(
                quant_backend="bitsandbytes_8bit",
                quant_kwargs={"load_in_8bit": True},
                components_to_quantize=["text_encoder"],
            )
            return Pipe.from_pretrained(
                MODEL_ID, torch_dtype=torch.bfloat16,
                cache_dir=self.models_dir,
                quantization_config=quant).to("cuda")
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
        return Pipe.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16,
            cache_dir=self.models_dir,
            quantization_config=quant).to("cuda")

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
