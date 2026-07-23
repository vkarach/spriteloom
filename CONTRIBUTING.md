# Contributing

Thanks for taking a look. Spriteloom is a Python WebSocket server plus an
Aseprite Lua extension.

## Setup

See the [README](README.md) install steps. You need an NVIDIA GPU with 12+ GB
of VRAM to run the model, but most of the code (protocol, postprocess, prompt
assembly, plugin UI) is testable **without** a GPU.

## Before opening a PR

Run the same checks CI runs:

```
# server
.venv\Scripts\python -m pytest server/tests/ --ignore=server/tests/smoke.py

# plugin (needs: scoop install lua luacheck)
luacheck plugin\*.lua plugin\tests\*.lua
lua plugin\tests\test_prompt.lua
lua plugin\tests\test_panel.lua
```

## Conventions

- Keep comments short — one line, and only when they carry a constraint the
  code can't show.
- Prompt assembly (`plugin/prompt.lua`) and postprocess are pure and
  unit-tested; add a test when you touch them.
- The plugin never modifies the user's existing pixels. Keep it that way.
