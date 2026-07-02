"""FLUX.2 Klein 4B: instruction-based sprite editing (view/pose changes)."""
import logging

from server.pipeline import upscale_for_model

MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"
STYLE_SUFFIX = ". Keep the same pixel art style, colors and character design."
STEPS = 4          # Klein is step-distilled; 4 steps is the documented default
GUIDANCE = 1.0
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
        import torch
        from diffusers import Flux2KleinPipeline
        log.info("loading %s (first run downloads ~8 GB)...", MODEL_ID)
        self._pipe = Flux2KleinPipeline.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16,
            cache_dir=self.models_dir).to("cuda")
        log.info("instruct pipeline ready")

    def _cb(self, on_progress, done, total):
        if on_progress is None:
            return None
        def cb(pipe, step, timestep, kw):
            frac = (done + (step + 1) / STEPS) / total
            on_progress(min(1.0, frac))
            return kw
        return cb

    def edit_by_instruction(self, instruction, image, variants=4,
                            on_progress=None):
        self.load()
        big = upscale_for_model(image.convert("RGB"), max_side=MAX_SIDE)
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
            out.extend(imgs)
        return [i.convert("RGBA") for i in out]
