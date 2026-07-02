"""Full pipeline without Aseprite: prompt in, PNG files out in output/.

Usage:
  .venv\\Scripts\\python -m server.tests.smoke "demonic sword, dark-red hilt, black blade"
  .venv\\Scripts\\python -m server.tests.smoke "..." --size 64 --variants 2
  .venv\\Scripts\\python -m server.tests.smoke "horse standing on two legs" --edit samples\\konek-tobey.png --size 70 --strength 0.6
  .venv\\Scripts\\python -m server.tests.smoke "..." --edit src.png --inpaint-mask mask.png
"""
import argparse
import pathlib
import sys
import time

from server.pipeline import Pipeline
from server.postprocess import downscale, subject_palette, snap_to_palette, \
    remove_background


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--size", type=int, default=64)
    ap.add_argument("--variants", type=int, default=4)
    ap.add_argument("--edit", metavar="PNG", help="img2img source sprite")
    ap.add_argument("--strength", type=float, default=0.6)
    ap.add_argument("--inpaint-mask", metavar="PNG",
                    help="with --edit: white-on-black mask -> inpaint mode")
    ap.add_argument("--instruct", action="store_true",
                    help="with --edit: treat prompt as an instruction (Klein)")
    args = ap.parse_args()

    out_dir = pathlib.Path("output")
    out_dir.mkdir(exist_ok=True)
    if not (args.instruct and args.edit):
        pipe = Pipeline()
        t0 = time.time()
        pipe.load()
        print(f"model loaded in {time.time() - t0:.1f}s", flush=True)

    from PIL import Image as PILImage
    progress = lambda v: print(f"\r{v:4.0%}", end="")
    t0 = time.time()
    if args.instruct and args.edit:
        from server.instruct import InstructPipeline
        ipipe = InstructPipeline()
        t0 = time.time()
        ipipe.load()
        print(f"instruct model loaded in {time.time() - t0:.1f}s", flush=True)
        src = PILImage.open(args.edit).convert("RGBA")
        images = ipipe.edit_by_instruction(
            args.prompt, src, variants=args.variants, on_progress=progress)
    elif args.edit and args.inpaint_mask:
        src = PILImage.open(args.edit).convert("RGBA")
        mask = PILImage.open(args.inpaint_mask)
        images = pipe.inpaint(args.prompt, src, mask,
                              variants=args.variants, on_progress=progress)
    elif args.edit:
        src = PILImage.open(args.edit).convert("RGBA")
        images = pipe.img2img(args.prompt, src, strength=args.strength,
                              variants=args.variants, on_progress=progress)
    else:
        images = pipe.txt2img(args.prompt, variants=args.variants,
                              on_progress=progress)
    print(f"\ngenerated in {time.time() - t0:.1f}s")

    stem = "".join(c if c.isalnum() else "_" for c in args.prompt)[:40]
    from server.postprocess import sprite_palette
    src_pal = None
    if args.edit:
        src_pal = sprite_palette(PILImage.open(args.edit).convert("RGBA"))
    for n, img in enumerate(images):
        img.save(out_dir / f"{stem}_{n}_raw.png")
        cut = remove_background(img, tolerance=16)
        small = downscale(cut, (args.size, args.size))
        small = snap_to_palette(small, src_pal or subject_palette(cut, 16))
        small.save(out_dir / f"{stem}_{n}.png")
    print(f"wrote {len(images) * 2} files to {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
