"""Launch the Camera Calibration GUI with Python 3.10 compatibility."""
import sys
from pathlib import Path

# Patch StrEnum for Python < 3.11
if sys.version_info < (3, 11):
    from strenum import StrEnum
    import enum
    enum.StrEnum = StrEnum

from gui_app.app import main

main()
