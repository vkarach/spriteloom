"""FLUX.2 Klein 4B: instruction-based sprite editing and text-to-image.

Two separate pipelines because the 8-bit quantized transformer works for
editing but produces pure noise in text-to-image; t2i runs bf16 with CPU
offload instead (~16 GB system RAM while loaded).
"""
import logging

from PIL import Image

MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"
STYLE_SUFFIX = ". Keep the same pixel art style, colors and character design."
STEPS = 8          # Klein is step-distilled (4 = documented default); 8 costs
                   # 2x time but visibly steadies small features like eyes
GUIDANCE = 1.0
T2I_STEPS = 8      # t2i is also step-distilled; 8 keeps the flat sprite look
                   # (more steps drift toward realism), ~6x faster than 50
T2I_SUFFIX = (" Flat 2D pixel art game sprite, crisp pixels, flat colors,"
              " clean outlines, single centered object on a plain solid"
              " background.")
# Klein 4B already needs ~13 GB; a batched 1024px run overflows 16 GB and
# WDDM starts paging VRAM through system RAM (observed: 10x+ slowdown).
# 512px input is plenty for pixel art, and variants run one at a time.
MAX_SIDE = 512

log = logging.getLogger("spriteforge.instruct")


class InstructPipeline:
    def __init__(self, models_dir: str = "models"):
        self.models_dir = models_dir
        self._pipe = None

    def load(self):
        if self._pipe is not None:
            return
        import gc
        import torch
        from diffusers import Flux2KleinPipeline
        log.info("loading %s (first run downloads ~8 GB)...", MODEL_ID)
        try:
            # 8-bit transformer (official BFL weights, quantized on load):
            # the whole pipeline then fits in VRAM (~11.5 GB), so no CPU
            # offload - which used to keep ~16 GB of weights in system RAM.
            from diffusers import PipelineQuantizationConfig
            quant = PipelineQuantizationConfig(
                quant_backend="bitsandbytes_8bit",
                quant_kwargs={"load_in_8bit": True},
                components_to_quantize=["transformer"],
            )
            self._pipe = Flux2KleinPipeline.from_pretrained(
                MODEL_ID, torch_dtype=torch.bfloat16,
                cache_dir=self.models_dir,
                quantization_config=quant).to("cuda")
        except Exception:
            log.exception("8-bit load failed; falling back to CPU offload "
                          "(uses ~16 GB of system RAM)")
            self._pipe = Flux2KleinPipeline.from_pretrained(
                MODEL_ID, torch_dtype=torch.bfloat16,
                cache_dir=self.models_dir)
            self._pipe.enable_model_cpu_offload()
        gc.collect()
        torch.cuda.empty_cache()
        log.info("instruct pipeline ready")

    def _cb(self, on_progress, done, total):
        if on_progress is None:
            return None
        def cb(pipe, step, timestep, kw):
            frac = (done + (step + 1) / STEPS) / total
            on_progress(min(1.0, frac))
            return kw
        return cb

    @staticmethod
    def _prep_input(image):
        """Integer-factor upscale + pad to model-friendly dims.

        The model mimics the pixel grid it is shown. A fractional upscale
        (70px -> 512 is x7.31) shows it stretched pixels and it answers with
        off-grid, wobbly shapes (crooked eye frames). Integer scaling keeps
        the grid honest; the padding to a multiple of 16 is cropped off the
        output before postprocessing.
        """
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
                            on_progress=None):
        self.load()
        big, (bw, bh) = self._prep_input(image)
        out = []
        for i in range(variants):  # one at a time: batching overflows VRAM
            imgs = self._pipe(
                prompt=instruction + STYLE_SUFFIX,
                image=big,
                width=big.size[0], height=big.size[1],
                guidance_scale=GUIDANCE,
                num_inference_steps=STEPS,
                num_images_per_prompt=1,
                callback_on_step_end=self._cb(on_progress, i, variants),
            ).images
            out.extend(img.crop((0, 0, bw, bh)) for img in imgs)
        return [i.convert("RGBA") for i in out]


def t2i_size(target_size: tuple[int, int],
             max_side: int = MAX_SIDE) -> tuple[int, int]:
    """Generation size matching the target aspect: long side ~max_side,
    both dims multiples of 16."""
    w, h = target_size
    scale = max_side / max(w, h)
    return (max(16, round(w * scale / 16) * 16),
            max(16, round(h * scale / 16) * 16))


class KleinT2I:
    """Klein text-to-image, fully VRAM-resident: 8-bit text encoder + bf16
    transformer (~11.5 GB). Quantizing the TRANSFORMER breaks t2i (pure
    noise), but the text encoder takes 8-bit fine - and with both resident
    there is no per-call offload streaming (was ~20 s per generation)."""

    def __init__(self, models_dir: str = "models"):
        self.models_dir = models_dir
        self._pipe = None

    def load(self):
        if self._pipe is not None:
            return
        import gc
        import torch
        from diffusers import Flux2KleinPipeline
        log.info("loading %s for t2i...", MODEL_ID)
        try:
            from diffusers import PipelineQuantizationConfig
            quant = PipelineQuantizationConfig(
                quant_backend="bitsandbytes_8bit",
                quant_kwargs={"load_in_8bit": True},
                components_to_quantize=["text_encoder"],
            )
            self._pipe = Flux2KleinPipeline.from_pretrained(
                MODEL_ID, torch_dtype=torch.bfloat16,
                cache_dir=self.models_dir,
                quantization_config=quant).to("cuda")
        except Exception:
            log.exception("resident load failed; falling back to CPU "
                          "offload (slower, ~16 GB of system RAM)")
            self._pipe = Flux2KleinPipeline.from_pretrained(
                MODEL_ID, torch_dtype=torch.bfloat16,
                cache_dir=self.models_dir)
            self._pipe.enable_model_cpu_offload()
        self._pipe.vae.enable_slicing()
        gc.collect()
        torch.cuda.empty_cache()
        log.info("t2i pipeline ready")

    def _cb(self, on_progress, done, chunk, total):
        if on_progress is None:
            return None
        def cb(pipe, step, timestep, kw):
            frac = (done + chunk * (step + 1) / T2I_STEPS) / total
            on_progress(min(1.0, frac))
            return kw
        return cb

    def txt2img(self, prompt, target_size, variants=4, on_progress=None):
        self.load()
        w, h = t2i_size(target_size)
        out = []
        # Batched in chunks of 4: peaks ~12 GB VRAM with resident weights.
        while len(out) < variants:
            chunk = min(4, variants - len(out))
            out += self._pipe(
                prompt=prompt + T2I_SUFFIX,
                width=w, height=h,
                num_inference_steps=T2I_STEPS,
                num_images_per_prompt=chunk,
                callback_on_step_end=self._cb(on_progress, len(out), chunk,
                                              variants),
            ).images
        return [i.convert("RGBA") for i in out]
