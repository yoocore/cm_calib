"""PySide6 desktop GUI for local camera calibration orchestration."""

import sys
from pathlib import Path


def bundled_path(filename: str) -> str:
    """Resolve bundled resource file path (works in dev and PyInstaller frozen mode).

    In dev mode:  <this_file_dir>/<filename>
    In exe mode:  sys._MEIPASS/src/gui_app/<filename>
    """
    if getattr(sys, "frozen", False):
        return (Path(sys._MEIPASS) / "src" / "gui_app" / filename).as_posix()
    return (Path(__file__).resolve().parent / filename).as_posix()
