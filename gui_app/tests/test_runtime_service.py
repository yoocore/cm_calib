from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from gui_app.services.runtime_service import RuntimeService


class TestRuntimeService:
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
