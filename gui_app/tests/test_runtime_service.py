from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from PySide6.QtCore import QProcessEnvironment

from gui_app.services.runtime_service import RuntimeService


class TestRuntimeService:
    def test_probe_status_passes_cm_install(self, tmp_path):
        project_root = tmp_path / "project"
        calibration_root = project_root / "Data" / "Script" / "CameraCalibration"
        calibration_root.mkdir(parents=True)

        service = RuntimeService(project_root)
        service.process_service.start_python = MagicMock()

        cm_install = Path("D:/IPG/carmaker/win64-14.2")
        service.probe_status(project_root, "testrun", verify_health=True, cm_install=cm_install)

        args, kwargs = service.process_service.start_python.call_args
        script_path, arguments, working_dir = args
        assert "--cm-install" in arguments
        idx = arguments.index("--cm-install")
        assert "win64-14.2" in arguments[idx + 1]
        assert "--health-check-after-start" in arguments

    def test_prepare_runtime_passes_cm_install(self, tmp_path):
        project_root = tmp_path / "project"
        calibration_root = project_root / "Data" / "Script" / "CameraCalibration"
        calibration_root.mkdir(parents=True)

        service = RuntimeService(project_root)
        service.process_service.start_python = MagicMock()

        cm_install = Path("D:/IPG/carmaker/win64-14.2")
        service.prepare_runtime(project_root, "testrun", cameras=["cam1"], cm_install=cm_install)

        args, kwargs = service.process_service.start_python.call_args
        script_path, arguments, working_dir = args
        assert "--cm-install" in arguments
        idx = arguments.index("--cm-install")
        assert "win64-14.2" in arguments[idx + 1]

    def test_prepare_runtime_skips_cm_install_when_none(self, tmp_path):
        project_root = tmp_path / "project"
        calibration_root = project_root / "Data" / "Script" / "CameraCalibration"
        calibration_root.mkdir(parents=True)

        service = RuntimeService(project_root)
        service.process_service.start_python = MagicMock()

        service.prepare_runtime(project_root, "testrun", cameras=["cam1"], cm_install=None)

        args, kwargs = service.process_service.start_python.call_args
        script_path, arguments, working_dir = args
        assert "--cm-install" not in arguments

    def test_prepare_runtime_passes_multiple_cameras(self, tmp_path):
        project_root = tmp_path / "project"
        calibration_root = project_root / "Data" / "Script" / "CameraCalibration"
        calibration_root.mkdir(parents=True)

        service = RuntimeService(project_root)
        service.process_service.start_python = MagicMock()

        service.prepare_runtime(project_root, "testrun", cameras=["cam1", "cam2"])

        args, kwargs = service.process_service.start_python.call_args
        script_path, arguments, working_dir = args
        cam_sensor_indices = [i for i, a in enumerate(arguments) if a == "--camera-sensor"]
        assert len(cam_sensor_indices) == 2
        assert arguments[cam_sensor_indices[0] + 1] == "cam1"
        assert arguments[cam_sensor_indices[1] + 1] == "cam2"

    def test_prepare_runtime_adds_versioned_cmapi_pythonpath(self, tmp_path):
        project_root = tmp_path / "project"
        calibration_root = project_root / "Data" / "Script" / "CameraCalibration"
        calibration_root.mkdir(parents=True)
        cm_install = tmp_path / "D" / "IPG" / "carmaker" / "win64-14.1"
        (cm_install / "Python" / "python3.10" / "cmapi").mkdir(parents=True)

        service = RuntimeService(project_root)
        service.process_service.start_python = MagicMock()

        service.prepare_runtime(project_root, "testrun", cameras=["cam1"], cm_install=cm_install)

        env = service.process_service._process.processEnvironment()
        pythonpath = env.value("PYTHONPATH", "")
        assert str((cm_install / "Python" / "python3.10").resolve()) in pythonpath
