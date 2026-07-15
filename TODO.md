# TODO

- One Klein pipeline instead of two: the t2i config (8-bit text encoder +
  bf16 transformer, fully resident, ~11.5 GB) can likely serve editing
  too - then the 8-bit edit pipeline and the klein/klein_t2i model swap
  disappear, and edit quality may improve (bf16 transformer).

- Edit / Inpaint still run on SDXL: consider Klein image-to-image for
  detailed edit prompts once its editing quality at low strength is tested.
- 8-bit quantized Klein transformer outputs pure noise in text-to-image
  (works for editing) — investigate or report upstream; t2i currently runs
  bf16 + CPU offload as a workaround.
