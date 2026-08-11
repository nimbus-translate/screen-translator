"""PyInstaller runtime hook：让 paddle 在冻结环境里能找到自己的 DLL 目录。"""

import os
import sys

_base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.argv[0])))
_libs = os.path.join(_base, "paddle", "libs")
if os.path.isdir(_libs):
    try:
        os.add_dll_directory(_libs)
    except AttributeError:
        pass
    os.environ["PATH"] = _libs + os.pathsep + os.environ.get("PATH", "")
