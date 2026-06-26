from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.entry.portable_runtime import apply_cmapi_to_current_process, ensure_calibration_root_on_sys_path


def _patch_strenum() -> None:
    if sys.version_info >= (3, 11):
        return
    import enum

    from strenum import StrEnum

    enum.StrEnum = StrEnum
    sys.modules["enum"].StrEnum = StrEnum


def _dispatch_embedded_command() -> bool:
    if len(sys.argv) < 3 or sys.argv[1] != "--camcal-dispatch":
        return False

    script_path = Path(sys.argv[2]).resolve()
    if not script_path.exists():
        raise SystemExit(f"Dispatch target not found: {script_path}")

    script_dir = str(script_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    sys.argv = [str(script_path), *sys.argv[3:]]
    runpy.run_path(str(script_path), run_name="__main__")
    return True


def main() -> int:
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.screen=false"
    import warnings
    warnings.filterwarnings("ignore", message=".*compiled using NumPy 1.*")
    _patch_strenum()
    ensure_calibration_root_on_sys_path()
    apply_cmapi_to_current_process()

    if _dispatch_embedded_command():
        return 0

    from src.gui_app.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
