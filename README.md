# SpriteForge

**Local AI pixel-art assistant for [Aseprite](https://www.aseprite.org/).**
Generate sprites from a text prompt, edit existing sprites with an
instruction, or redraw a selected region — all running on your own GPU.
No cloud, no subscription, your pixels never leave your machine.

**[Sample output and how it works → vkarach.github.io/sprite-forge](https://vkarach.github.io/sprite-forge/)**

<!-- Add the screencast GIF here once recorded; see assets/demo.gif -->
<!-- ![SpriteForge in action](assets/demo.gif) -->

<p align="center">
  <img src="assets/gallery/chest.png" width="110" alt="treasure chest">
  <img src="assets/gallery/potion.png" width="110" alt="green potion">
  <img src="assets/gallery/lantern.png" width="110" alt="iron lantern">
  <img src="assets/gallery/cottage.png" width="110" alt="wooden cottage">
  <br>
  <img src="assets/gallery/gate.png" width="110" alt="castle gate">
  <img src="assets/gallery/fox.png" width="110" alt="red fox">
  <img src="assets/gallery/dragon.png" width="110" alt="orange dragon">
  <img src="assets/gallery/knight.png" width="110" alt="armored knight">
</p>
<p align="center"><em>Real output, one generation each. Subjects like "wooden
treasure chest with iron bands", "red fox with a bushy tail", "knight in steel
plate armor". Scaled up with hard pixel edges for display.</em></p>

## What it is

A WebSocket server that runs a diffusion model locally, plus an Aseprite
extension that talks to it. You stay in Aseprite the whole time; results
open in a side window and drop in as new layers on click. It never edits
your existing pixels.

Four tasks, one panel:

- **Generate** — a sprite from a text prompt.
- **Edit with AI** — change an existing sprite by instruction ("make the
  sword glow blue"); style and everything unmentioned stays put.
- **Inpaint Selection** — same, but only ever touches the selected region.
- **Rotate + Instruct** — re-view the same subject from another angle.

Everything runs on a single model (FLUX.2 Klein), so there are no model
swaps: after the first load, tasks respond in seconds.

## Hardware you need — read this first

This runs a 4B-parameter diffusion model on your own machine. It is **not**
a lightweight tool:

| | Requirement |
|---|---|
| GPU | **NVIDIA, 12+ GB VRAM** (developed on an RTX 5080) |
| OS | Windows |
| Python | 3.11+ |
| Aseprite | 1.3+ |
| Disk | ~15 GB for the model (downloaded on first use) |

No NVIDIA GPU with enough VRAM, no SpriteForge. There is no CPU fallback
and no cloud option by design — the whole point is that it runs locally.

## Install

1. Download this repository and run `SpriteForge.exe`.
2. Press **Setup**. It shows what is missing: the environment, the
   dependencies, PyTorch, the plugin, the model.
3. Tick what you want and press **Install selected**. It builds the `.venv`,
   installs the packages and the plugin, and prints a live log. Restart
   Aseprite once the plugin is in.

The model (~15 GB) is unticked by default; leave it, and the server downloads
it the first time you run a task, or tick it to fetch it up front.

If you would rather do it by hand:

1. `py -3 -m venv .venv`
2. `.venv\Scripts\python -m pip install -r server\requirements.txt`
3. `.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cu128`
4. `install-plugin.bat`, then restart Aseprite.

To build the exe yourself: `.venv\Scripts\python -m pip install -r
launcher\requirements.txt`, then `.venv\Scripts\python -m PyInstaller
build.spec`, and copy `dist\SpriteForge.exe` next to `.venv`. It is a
launcher only, about 15 MB: the model and PyTorch stay outside it.

## Use

1. Run `SpriteForge.exe`, press **START** and leave the window open. The dot
   turns green once the model is resident, about 25 seconds after a warm
   start. Closing the window stops the server. (`start-server.bat` still
   works if you prefer a console.)
2. In Aseprite: **Sprite → SpriteForge...** (or press **F1**). Pick a task,
   fill the fields, press **Run**. Results open in a separate window; click
   a variant to insert it as a new layer.
3. **Generate** understands full sentences: pick a **View** preset, name the
   **Subject** ("closed book with dark brown leather cover"), add **Extra**
   details if needed — the panel shows the exact text it will send.
4. **Edit / Inpaint** take instructions, not a strength slider: say what to
   change and how much. Inpaint only touches the selection.
5. **Advanced...** opens a separate window with Background, Palette, and Seed,
   so the main panel never resizes.
6. **Background**: Auto detects and strips a uniform background, Remove
   strips the dominant border color, Keep leaves it fully opaque.
7. **Palette**: Auto derives colors per result; Current palette pins output to
   the open sprite's whole palette; Selected colors pins to only the swatches
   highlighted in the palette bar; Palette file pins to a `.gpl`/`.pal`/`.png`
   file so a batch of sprites shares one set of colors.
8. **History** browses past generations (stored in `output/`), newest first;
   click a run to see its variants, click a variant to insert it.
9. **Rotate / Instruct**: name the subject explicitly ("four-legged brown
   horse", not "character"), and enable Mirror symmetry for front/back views.

## How it works

```
Aseprite plugin (Lua)  <--WebSocket-->  Python server  -->  FLUX.2 Klein (GPU)
   dialogs, results,                     protocol,           single resident
   history, layer insert                 postprocess         model, no swaps
```

- **One model, always resident.** Every task hits the same FLUX.2 Klein
  pipeline, so there is no per-task load/unload — after the first warm-up,
  latency is dominated by inference, not I/O.
- **WebSocket protocol** with request validation at the boundary and
  streamed progress messages back to the plugin (`server/protocol.py`,
  `server/main.py`).
- **Postprocess pipeline** turns raw diffusion output into a clean sprite:
  crop to subject, palette quantization, background removal, mirror
  symmetry, fit-into-canvas (`server/postprocess.py`).
- **The plugin is modular Lua** with pure, unit-tested prompt assembly and a
  UI layer tested against a stubbed Aseprite API — see below.

### Quantization note

8-bit quantizing the Klein *transformer* produces pure noise in
text-to-image (edits work fine). The shipped setup sidesteps this: 8-bit
text encoder + bf16 transformer, fully resident. See `TODO.md`.

## Development

**Server tests:**

```
.venv\Scripts\python -m pytest server/tests/ --ignore=server/tests/smoke.py
```

**Prompt tuning without Aseprite** (writes raw + postprocessed variants to `output/`):

```
.venv\Scripts\python -m server.tests.smoke "demonic sword" --size 64
```

**Plugin tests** (needs `scoop install lua luacheck`, user-scoped):

```
luacheck plugin\*.lua plugin\tests\*.lua
lua plugin\tests\test_prompt.lua
lua plugin\tests\test_panel.lua
```

`luacheck` bundles Lua 5.4 (the version Aseprite runs); `.luacheckrc`
declares the API globals it injects. `test_panel.lua` loads every module
against a stubbed Aseprite API and repaints the status canvas in each server
state — this catches broken cross-module calls without launching the editor.
Layout and ghosting still need a real Aseprite.

### Plugin layout

| file | holds |
|---|---|
| `main.lua` | entry point, registers the menu command |
| `dialogs.lua` | the control panel |
| `results.lua` | results window (fresh variants) |
| `history.lua` | history list and single-run windows |
| `ui.lua` | theme colors, checkerboard, variant grid, prompt preview |
| `sprite.lua` | frame/mask export, inserting variants as layers |
| `prompt.lua` | prompt assembly and key maps (pure Lua, unit-tested) |
| `client.lua` | WebSocket client |
| `base64.lua` | base64 codec |

### Launcher layout

| file | holds |
|---|---|
| `launcher/app.py` | the window, the JS bridge, window sizing |
| `launcher/ui/index.html` | markup and styles, main and setup screens |
| `launcher/server_proc.py` | the server subprocess, port probing, health |
| `launcher/plugin_install.py` | copying the plugin, version comparison |
| `launcher/paths.py` | finding the root, Python, Aseprite and the model |
| `launcher/setup_checks.py` | what the setup is missing, detection only |
| `launcher/setup_steps.py` | running the install steps in order |
| `server/config.py` | the settings, shared by the launcher and the server |

The launcher owns the settings file `%APPDATA%\SpriteForge\config.json`: the
port, the VRAM mode, and the setup paths all live there, and every write
merges so one key never erases another. The port defaults to 8765, and
**Install** stamps it into `server.json` next to the plugin, so both ends
agree.

The server runs inside a Windows job object that dies with the launcher.
That is what keeps a crashed or killed launcher from leaving a server behind
holding your VRAM.

## License

[Apache 2.0](LICENSE). The model, FLUX.2 Klein 4B, is licensed separately by
Black Forest Labs under Apache 2.0 and downloaded at runtime, not redistributed
here.
