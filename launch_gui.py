"""Launch the Camera Calibration GUI with Python 3.10 compatibility."""
import os
import sys
from pathlib import Path

# Patch StrEnum for Python < 3.11
if sys.version_info < (3, 11):
    from strenum import StrEnum
    import enum
    enum.StrEnum = StrEnum

# Auto-detect CarMaker Python API (cmapi) and add to PYTHONPATH
_CM_ROOTS = [
    "D:/IPG/carmaker", "C:/IPG/carmaker",
    "D:/IPG", "C:/IPG",
    "D:/CarMaker", "C:/CarMaker",
    "D:/Program Files/IPG/carmaker", "C:/Program Files/IPG/carmaker",
    "D:/Program Files/CarMaker", "C:/Program Files/CarMaker",
]
_found = False
for _root in _CM_ROOTS:
    _rp = Path(_root)
    if not _rp.is_dir():
        continue
    for _entry in sorted(_rp.iterdir(), reverse=True):
        if not _entry.name.startswith("win64-"):
            continue
        for _sub in ("Python/Lib/site-packages", "Python/Lib", "pylib"):
            _p = _entry / _sub
            if any((_p / c).exists() for c in ("cmapi", "cmapi.py", "cmapi.pyd")):
                _old = os.environ.get("PYTHONPATH", "")
                os.environ["PYTHONPATH"] = f"{_p};{_old}" if _old else str(_p)
                _found = True
                break
        if _found:
            break
    if _found:
        break

from gui_app.app import main

main()
