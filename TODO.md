# TODO

- 8-bit quantized Klein TRANSFORMER outputs pure noise in text-to-image,
  but editing (img2img-style, starting from a real image latent) is fine
  on the same 8-bit transformer (`server/instruct.py`, `components_to_quantize`
  only ever lists `text_encoder` because of this). Only tried
  `quant_backend="bitsandbytes_8bit"` so far — haven't checked whether NF4
  or fp8 quantization hit the same failure, or whether this is a known
  FLUX.2 issue upstream (diffusers/bitsandbytes trackers). Worth a minimal
  repro (fixed seed, quantize transformer only, compare t2i vs img2img
  output) before reporting upstream. Current setup sidesteps it entirely:
  8-bit text encoder + bf16 transformer, fully resident, ~11.5 GB VRAM.

- Legacy manual-install mode for Linux (no launcher.exe, CLI-only, matching
  the "by hand" README section). Server code already degrades cleanly off
  Windows (`memory_status()` in `server/instruct.py` returns None, callers
  handle it). What's actually needed: an `install-plugin.sh` copying to
  `~/.config/aseprite/extensions/spriteloom` instead of `%APPDATA%`, a
  trivial `start-server.sh`, and confirming the cu128 torch wheel resolves
  on Linux. Untested on real Linux + NVIDIA hardware — driver setup,
  Aseprite as a Flatpak/Snap (may sandbox the extensions path or the
  WebSocket), and CUDA version matching are unverified. Don't advertise as
  supported until someone actually runs it.
