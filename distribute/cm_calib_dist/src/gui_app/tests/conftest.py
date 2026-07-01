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
    from src.gui_app.main_window import MainWindow
    project_root = tmp_path / "project"
    (project_root / "Data" / "Script" / "CameraCalibration" / "configs").mkdir(parents=True)
    (project_root / "Movie").mkdir()
    (project_root / "Data" / "TestRun").mkdir(parents=True)
    testrun_file = project_root / "Data" / "TestRun" / "vctc_ngxpro"
    testrun_file.write_text(
        "#INFOFILE1.1 (UTF-8) - Do not remove this line!\n"
        "FileIdent = CarMaker-TestRun 14\n"
        "Vehicle = Examples/TestVehicle\n",
        encoding="utf-8",
    )
    (project_root / "Data" / "Vehicle" / "Examples").mkdir(parents=True)
    vehicle_file = project_root / "Data" / "Vehicle" / "Examples" / "TestVehicle"
    vehicle_file.write_text(
        "Sensor.0.name = cam1\n"
        "Sensor.0.Active = 1\n"
        "Sensor.Param.0.Type = CameraRSI\n"
        "Sensor.Param.0.Name = cam1\n",
        encoding="utf-8",
    )
    camera_config = project_root / "Data" / "Script" / "CameraCalibration" / "configs" / "camera.cam1.json"
    camera_config.write_text('{"camera": "cam1"}', encoding="utf-8")
    win = MainWindow(project_root)
    win.cm_settings_panel.testrun_edit.setText("vctc_ngxpro")
    win._on_testrun_changed("vctc_ngxpro")
    for i in range(win.cm_settings_panel.camera_list.count()):
        item = win.cm_settings_panel.camera_list.item(i)
        item.setCheckState(Qt.Checked)
    qtbot.addWidget(win)
    return win
