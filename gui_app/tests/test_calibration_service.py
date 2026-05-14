from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from gui_app.models.state import CalibrationLaunchConfig
from gui_app.services.calibration_service import CalibrationService


class TestCalibrationService:
    def test_start_passes_skip_prepare_for_first_camera(self, tmp_path):
        project_root = tmp_path / "project"
        calibration_root = project_root / "Data" / "Script" / "CameraCalibration"
        calibration_root.mkdir(parents=True)

        service = CalibrationService(project_root)
        service.process_service.start_python = MagicMock()

        launch = CalibrationLaunchConfig(
            project_root=project_root,
            testrun="testrun",
            cameras=["cam1"],
            skip_prepare_for_first_camera=True,
        )
        service.start(launch)

        args, _kwargs = service.process_service.start_python.call_args
        _script_path, arguments, _working_dir = args
        assert "--skip-prepare-for-first-camera" in arguments
