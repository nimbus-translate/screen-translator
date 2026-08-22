# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
import re
import tempfile

from PyInstaller.utils.hooks import collect_all, collect_submodules


build_version = os.environ.get("SCREEN_TRANSLATOR_BUILD_VERSION", "0.2.5")
if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", build_version):
    raise ValueError(f"Invalid SCREEN_TRANSLATOR_BUILD_VERSION: {build_version!r}")
metadata_dir = Path(tempfile.mkdtemp(prefix="screen-translator-build-"))
version_file = metadata_dir / "build-version.txt"
version_file.write_text(build_version, encoding="ascii")

datas = [
    (os.path.join("assets", "app_launch_v4.png"), "assets"),
    (str(version_file), "."),
]
binaries = []
hiddenimports = []

# Keep the light build intentionally narrow. PyInstaller's Qt hooks collect
# only the Qt modules imported by the application; collecting all of PySide6
# would pull WebEngine, QML, multimedia and database plugins into the package.
for package in (
    "mss",
    "pynput",
    "winocr",
    "winrt.runtime",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.globalization",
    "winrt.windows.graphics.imaging",
    "winrt.windows.media.ocr",
    "winrt.windows.storage.streams",
):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception:
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

hiddenimports += collect_submodules("pynput", filter=lambda name: "._win32" in name or name.endswith("._base"))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "cv2",
        "matplotlib",
        "paddle",
        "paddleocr",
        "paddlex",
        "scipy",
        "sklearn",
        "tkinter",
        "torch",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ScreenTranslator-Lite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/app_launch_v4.ico",
)
