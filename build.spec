# -*- mode: python ; coding: utf-8 -*-

import os

import importlib.metadata
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
datas.append((os.path.join("assets", "app_launch_v4.png"), "assets"))

for pkg in ("PySide6", "mss", "pynput", "paddle", "paddleocr", "paddlex", "cv2", "PIL"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# PaddleX 用 importlib.metadata 校验 OCR 可选依赖，必须把依赖的 dist-info 一并打进包
_PADDLEX_OCR_DEPS = [
    "beautifulsoup4", "einops", "ftfy", "imagesize", "Jinja2", "latex2mathml",
    "lxml", "opencv-contrib-python", "openpyxl", "premailer", "pyclipper",
    "pypdfium2", "python-bidi", "regex", "safetensors", "scikit-learn",
    "scipy", "sentencepiece", "shapely", "tiktoken", "tokenizers",
]
for _dep in _PADDLEX_OCR_DEPS:
    try:
        _dist = importlib.metadata.distribution(_dep)
        _dist_dir = _dist._path  # dist-info 目录本身
        datas.append((_dist_dir, os.path.basename(_dist_dir)))
    except Exception:
        pass

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["paddle_runtime_hook.py"],
    # PaddleX imports sklearn at module load; sklearn requires scipy even for
    # the OCR-only pipeline. Excluding scipy makes the frozen OCR registry fail.
    excludes=["tkinter", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ScreenTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/app_launch_v4.ico",
)
