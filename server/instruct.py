"""FLUX.2 Klein 4B: instruction-based sprite editing (view/pose changes)."""
import logging

from PIL import Image

MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"
STYLE_SUFFIX = ". Keep the same pixel art style, colors and character design."
STEPS = 8          # Klein is step-distilled (4 = documented default); 8 costs
                   # 2x time but visibly steadies small features like eyes
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
            cache_dir=self.models_dir)
        # Klein + its text encoder nearly fill 16 GB and trigger WDDM paging
        # when resident all at once; sequential offload keeps only the active
        # component on the GPU and is faster in practice on this card.
        self._pipe.enable_model_cpu_offload()
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
