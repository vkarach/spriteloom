"""SDXL + pixel-art LoRA. Generates at ~1024px; postprocess shrinks it."""
import logging

from PIL import Image

MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
LORA_ID = "nerijs/pixel-art-xl"
VAE_ID = "madebyollin/sdxl-vae-fp16-fix"  # decodes in fp16 without artifacts
PROMPT_SUFFIX = (", pixel art, flat colors, clean outlines, simple shapes,"
                 " single centered subject, plain background")
NEGATIVE = ("blurry, smooth shading, gradient, photo, 3d render, pattern,"
            " tiling, repeating, noisy, multiple objects, border, frame, text")
STEPS = 30

log = logging.getLogger("spriteforge.pipeline")


class Pipeline:
    def __init__(self, models_dir: str = "models"):
        self.models_dir = models_dir
        self._txt2img = None
        self._img2img = None
        self._inpaint = None

    def load(self):
        if self._txt2img is not None:
            return
        import torch
        from diffusers import (AutoencoderKL,
                               AutoPipelineForText2Image,
                               AutoPipelineForImage2Image,
                               AutoPipelineForInpainting)
        log.info("loading %s (first run downloads ~7 GB)...", MODEL_ID)
        vae = AutoencoderKL.from_pretrained(
            VAE_ID, torch_dtype=torch.float16, cache_dir=self.models_dir,
            low_cpu_mem_usage=True)
        self._txt2img = AutoPipelineForText2Image.from_pretrained(
            MODEL_ID, torch_dtype=torch.float16, variant="fp16", vae=vae,
            cache_dir=self.models_dir, low_cpu_mem_usage=True).to("cuda")
        self._txt2img.load_lora_weights(LORA_ID, cache_dir=self.models_dir)
        self._img2img = AutoPipelineForImage2Image.from_pipe(self._txt2img)
        self._inpaint = AutoPipelineForInpainting.from_pipe(self._txt2img)
        # Weights now live in VRAM; drop the CPU-side loading leftovers so
        # the server does not sit on gigabytes of host RAM.
        import gc
        gc.collect()
        torch.cuda.empty_cache()
        log.info("pipeline ready")

    def _cb(self, on_progress, total):
        if on_progress is None:
            return None
        def cb(pipe, step, timestep, kw):
            on_progress(min(1.0, (step + 1) / total))
            return kw
        return cb

    def txt2img(self, prompt, variants=4, on_progress=None):
        self.load()
        out = self._txt2img(
            prompt=prompt + PROMPT_SUFFIX, negative_prompt=NEGATIVE,
            num_inference_steps=STEPS, num_images_per_prompt=variants,
            width=1024, height=1024,
            callback_on_step_end=self._cb(on_progress, STEPS),
        ).images
        return [i.convert("RGBA") for i in out]

    def img2img(self, prompt, image, strength=0.6, variants=4,
                on_progress=None):
        self.load()
        big = upscale_for_model(image.convert("RGB"))
        steps = max(int(STEPS / max(strength, 0.05)), STEPS)
        out = self._img2img(
            prompt=prompt + PROMPT_SUFFIX, negative_prompt=NEGATIVE,
            image=big, strength=strength,
            num_inference_steps=steps, num_images_per_prompt=variants,
            callback_on_step_end=self._cb(on_progress, int(steps * strength)),
        ).images
        return [i.convert("RGBA") for i in out]

    def inpaint(self, prompt, image, mask, variants=4, on_progress=None):
        self.load()
        big = upscale_for_model(image.convert("RGB"))
        big_mask = mask.convert("L").resize(big.size, Image.NEAREST)
        out = self._inpaint(
            prompt=prompt + PROMPT_SUFFIX, negative_prompt=NEGATIVE,
            image=big, mask_image=big_mask, strength=0.99,
            num_inference_steps=STEPS, num_images_per_prompt=variants,
            width=big.size[0], height=big.size[1],
            callback_on_step_end=self._cb(on_progress, STEPS),
        ).images
        return [i.convert("RGBA") for i in out]


def upscale_for_model(img: Image.Image) -> Image.Image:
    """Nearest-neighbor upscale so SDXL sees crisp pixels; dims % 8 == 0."""
    w, h = img.size
    scale = 1024 / max(w, h)
    nw = max(8, round(w * scale / 8) * 8)
    nh = max(8, round(h * scale / 8) * 8)
    return img.resize((nw, nh), Image.NEAREST)
