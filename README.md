# SpriteForge

Local AI pixel-art assistant for Aseprite: generate sprites from text,
edit existing sprites with a prompt, or redraw a selected region.
Runs entirely on your own GPU — no subscriptions.

## Requirements

- Windows, NVIDIA GPU with 12+ GB VRAM (built on an RTX 5080)
- Python 3.11+
- Aseprite 1.3+

## Setup

1. `py -3 -m venv .venv`
2. `.venv\Scripts\python -m pip install -r server\requirements.txt`
3. `.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cu128`
4. `install-plugin.bat` (then restart Aseprite)

First generation downloads ~7 GB of model weights into `models/`. The
Rotate / Instruct task uses a second model (~8 GB, FLUX.2 Klein) downloaded
on its first use; switching between tasks that need different models takes
~30 s (only one model fits in VRAM at a time).

## Use

1. Run `start-server.bat` (keep the window open; minimize it). Stop it
   with Ctrl+C; cmd may ask "Terminate batch job (Y/N)?" — answer y.
2. In Aseprite: **Sprite → SpriteForge → Open SpriteForge**. One panel:
   pick the task (Generate / Edit with AI / Inpaint Selection /
   Rotate + Instruct), fill the fields, press Run. Results open in a
   separate window; click a variant to insert it as a new layer. Your
   pixels are never modified.
3. Generate runs on FLUX.2 Klein, which understands full sentences: pick a
   **View** preset, name the **Subject** ("closed book with dark brown
   leather cover"), add **Extra** details if needed — the panel shows the
   exact text it will send. "Custom (text only)" sends your subject text
   as-is.
4. The **Background** option controls transparency: Auto detects and strips
   a uniform background, Remove strips the dominant border color even when
   detection is unsure, Keep leaves the image fully opaque.
5. **History** opens past generations (stored in `output/`), newest first:
   page with Older/Newer, click a variant to insert it as a layer.
6. Rotate / Instruct tips: name the subject explicitly (e.g.
   "four-legged brown horse" — a generic "character" mutates it), use the
   Extra field for refinements, and enable Mirror symmetry for front/back
   views.

## Tuning without Aseprite

`.venv\Scripts\python -m server.tests.smoke "demonic sword" --size 64`
writes raw + postprocessed variants into `output/`.

## Tests

`.venv\Scripts\python -m pytest server/tests/ --ignore=server/tests/smoke.py`
