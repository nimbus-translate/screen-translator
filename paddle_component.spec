# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the optional PaddleOCR component only."""
import os
from pathlib import Path
import importlib.metadata
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for package in ("paddle", "paddleocr", "paddlex", "cv2", "numpy", "PIL"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

# PaddleX discovers OCR extras dynamically and validates their dist-info at
# runtime.  Include both package trees and distribution metadata; otherwise a
# frozen build can initialize the registry but fail only on the first OCR job.
PADDLEX_OCR_EXTRAS = {
    "beautifulsoup4": "bs4",
    "einops": "einops",
    "ftfy": "ftfy",
    "imagesize": "imagesize",
    "Jinja2": "jinja2",
    "latex2mathml": "latex2mathml",
    "lxml": "lxml",
    "opencv-contrib-python": "cv2",
    "openpyxl": "openpyxl",
    "premailer": "premailer",
    "pyclipper": "pyclipper",
    "pypdfium2": "pypdfium2",
    "python-bidi": "bidi",
    "regex": "regex",
    "safetensors": "safetensors",
    "scikit-learn": "sklearn",
    "scipy": "scipy",
    "sentencepiece": "sentencepiece",
    "shapely": "shapely",
    "tiktoken": "tiktoken",
    "tokenizers": "tokenizers",
}
for dependency, module in PADDLEX_OCR_EXTRAS.items():
    try:
        distribution = importlib.metadata.distribution(dependency)
        datas.append((distribution._path, os.path.basename(distribution._path)))
    except Exception:
        pass
    try:
        hiddenimports += collect_submodules(module)
    except Exception:
        pass

# Optional pre-warmed models: set PADDLE_COMPONENT_MODEL_DIR before building.
model_dir = os.environ.get("PADDLE_COMPONENT_MODEL_DIR")
if model_dir and Path(model_dir).is_dir():
    datas.append((model_dir, "models"))

a = Analysis(
    ["paddle_component_worker.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["paddle_runtime_hook.py"],
    excludes=["tkinter", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PaddleOCRComponent", console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PaddleOCRComponent")
