# TODO

- Generate's "Preparing prompt" phase (~20 s) dwarfs the 3 s of diffusion:
  CPU offload streams the text encoder + transformer to the GPU on every
  call. Investigate pinning the text encoder in VRAM (or quantizing only
  it) so repeat generations skip most of the streaming.
- One Klein pipeline instead of two: if bf16+offload handles editing at
  acceptable speed (with batched variants), the 8-bit edit pipeline and
  the klein/klein_t2i model swap disappear.

- Edit / Inpaint still run on SDXL: consider Klein image-to-image for
  detailed edit prompts once its editing quality at low strength is tested.
- 8-bit quantized Klein transformer outputs pure noise in text-to-image
  (works for editing) — investigate or report upstream; t2i currently runs
  bf16 + CPU offload as a workaround.
