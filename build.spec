# PyInstaller: the window only. The server runs from .venv and the model is
# downloaded at runtime, so none of the heavy stack belongs in here.
a = Analysis(
    ["launcher/app.py"],
    pathex=["."],
    datas=[("launcher/ui", "ui")],
    hiddenimports=["webview.platforms.edgechromium"],
    excludes=["torch", "diffusers", "transformers", "accelerate", "peft",
              "bitsandbytes", "safetensors", "scipy", "numpy", "PIL",
              "pytest", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="Spriteloom",
    console=False,
    icon="assets/spriteloom.ico",
    upx=False,
)
