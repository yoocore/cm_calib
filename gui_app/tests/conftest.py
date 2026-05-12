from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from strenum import StrEnum

# Inject StrEnum into the enum module for Python 3.10 compatibility
import enum
enum.StrEnum = StrEnum
sys.modules["enum"].StrEnum = StrEnum


@pytest.fixture
def main_window(qtbot, tmp_path):
    from gui_app.main_window import MainWindow
    project_root = tmp_path / "project"
    (project_root / "Data" / "Script" / "CameraCalibration" / "configs").mkdir(parents=True)
    (project_root / "Movie").mkdir()
    camera_config = project_root / "Data" / "Script" / "CameraCalibration" / "configs" / "camera.cam1.json"
    camera_config.write_text('{"camera": "cam1"}', encoding="utf-8")
    win = MainWindow(project_root)
    for i in range(win.calibration_panel.camera_list.count()):
        item = win.calibration_panel.camera_list.item(i)
        item.setCheckState(Qt.Checked)
    qtbot.addWidget(win)
    return win
