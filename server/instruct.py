"""FLUX.2 Klein 4B: text-to-image generation and instruction-based editing.

One resident pipeline (~11.5 GB VRAM): 8-bit text encoder + bf16
transformer. Quantizing the TRANSFORMER breaks t2i (pure noise); the text
encoder takes 8-bit fine, and the bf16 transformer also edits faster than
the 8-bit one did.
"""
import contextlib
import logging

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

log = logging.getLogger("spriteforge.instruct")


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
    """Mirror the nested tqdm bars of from_pretrained (pipeline components,
    checkpoint shards) into one 0..1 fraction. diffusers/transformers resolve
    tqdm at call time, so swapping the class catches their bars."""
    import tqdm as tqdm_lib
    import tqdm.auto as tqdm_auto
    real = tqdm_lib.tqdm
    stack = []  # active bars, outermost first

    def overall():
        f, w = 0.0, 1.0
        for bar in stack:
            t = getattr(bar, "total", None) or 0
            if t <= 0:
                continue
            f += w * min(bar.n / t, 1.0)
            w /= t
        return min(f, 1.0)

    class Mirror(real):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            stack.append(self)

        def update(self, n=1):
            out = super().update(n)
            report(overall())
            return out

        def close(self):
            if self in stack:
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
    def __init__(self, models_dir: str = "models"):
        self.models_dir = models_dir
        self._pipe = None

    def load(self):
        if self._pipe is not None:
            return
        import gc
        import torch
        from diffusers import Flux2KleinPipeline
        from server import models
        log.info("loading %s (first run downloads ~15 GB)...", MODEL_ID)
        with _report_tqdm(models.set_load_progress):
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
        log.info("klein pipeline ready")

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
        # Chunks of 2: image tokens double the sequence, ~12 GB peak.
        while len(out) < variants:
            chunk = min(2, variants - len(out))
            imgs = self._pipe(
                prompt=instruction + STYLE_SUFFIX,
                image=big,
                width=big.size[0], height=big.size[1],
                guidance_scale=GUIDANCE,
                num_inference_steps=STEPS,
                num_images_per_prompt=chunk,
                callback_on_step_end=self._cb(on_progress, len(out), chunk,
                                              variants),
            ).images
            out.extend(img.crop((0, 0, bw, bh)) for img in imgs)
        return [i.convert("RGBA") for i in out]

    def inpaint(self, prompt, image, mask, variants=4, on_progress=None):
        """Emulated inpaint: Klein edits the whole frame, but only the
        masked region of the result is kept - pixels outside the selection
        never change."""
        edits = self.edit_by_instruction(prompt, image, variants=variants,
                                         on_progress=on_progress)
        big_src = image.convert("RGBA").resize(edits[0].size, Image.NEAREST)
        big_mask = mask.convert("L").resize(edits[0].size, Image.NEAREST)
        out = []
        for e in edits:
            comp = big_src.copy()
            comp.paste(e, (0, 0), big_mask)
            out.append(comp)
        return out

    def txt2img(self, prompt, target_size, variants=4, on_progress=None):
        self.load()
        w, h = t2i_size(target_size)
        out = []
        # Chunks of 4: text-only sequences are shorter, ~12 GB peak.
        while len(out) < variants:
            chunk = min(4, variants - len(out))
            out += self._pipe(
                prompt=prompt + T2I_SUFFIX,
                width=w, height=h,
                num_inference_steps=STEPS,
                num_images_per_prompt=chunk,
                callback_on_step_end=self._cb(on_progress, len(out), chunk,
                                              variants),
            ).images
        return [i.convert("RGBA") for i in out]
