from __future__ import annotations

from unittest.mock import MagicMock

from gui_app.main_window import MainWindow
from gui_app.models.state import AppStatus, CalibrationLaunchConfig


class TestCalibStartFlow:
    """验证 Calib Start 的三级流水线：预检 → Prepare → 标定"""

    def test_start_calibration_precheck_fails(self, main_window: MainWindow, qtbot, mocker):
        """预检失败时不应调用 prepare 或 calibration"""
        mocker.patch("gui_app.main_window.QMessageBox")
        main_window.precheck_service.run_for_cameras = MagicMock(return_value=[
            {"camera": "cam1", "ok": False, "message": "missing movie file"}
        ])
        main_window.runtime_service.prepare_runtime = MagicMock()
        main_window.calibration_service.start = MagicMock()

        main_window._start_calibration()

        main_window.runtime_service.prepare_runtime.assert_not_called()
        main_window.calibration_service.start.assert_not_called()

    def test_start_calibration_precheck_ok_then_prepare(self, main_window: MainWindow, qtbot, mocker):
        """预检通过后应调用 prepare_runtime"""
        mocker.patch("gui_app.main_window.QMessageBox")
        from pathlib import Path
        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window.precheck_service.run_for_cameras = MagicMock(return_value=[
            {"camera": "cam1", "ok": True, "message": "ok"}
        ])
        main_window.runtime_service.prepare_runtime = MagicMock()

        main_window._start_calibration()

        main_window.runtime_service.prepare_runtime.assert_called_once()

    def test_runtime_summary_prepare_ready_triggers_calibration_start(self, main_window: MainWindow, mocker):
        """prepare 成功后 _on_runtime_summary 收到 status=ready + _pending_launch 应自动启动标定"""
        mocker.patch("gui_app.main_window.QMessageBox")
        main_window.calibration_service.start = MagicMock()
        main_window._pending_launch = CalibrationLaunchConfig(
            project_root=main_window.project_root,
            testrun="vctc_ngxpro",
            cameras=["cam1"],
        )

        main_window._on_runtime_summary({
            "mode": "prepare",
            "status": "ready",
            "vehicle": "TestVehicle",
            "active_sensors": ["cam1"],
            "process_counts": {"carmaker": 1, "gui_movie": 1, "gpusensor_movie": 0},
        })

        main_window.calibration_service.start.assert_called_once()
        assert main_window._pending_launch is None  # consumed

    def test_stop_calibration_during_preparing(self, main_window: MainWindow):
        """在 PREPARING 阶段点击 Stop 应停止 runtime_service"""
        main_window.state.status = AppStatus.PREPARING
        main_window._pending_launch = MagicMock()
        main_window.runtime_service.stop = MagicMock()
        main_window.calibration_service.stop = MagicMock()

        main_window._stop_calibration()

        main_window.runtime_service.stop.assert_called_once()
        main_window.calibration_service.stop.assert_not_called()
        assert main_window._pending_launch is None

    def test_stop_calibration_during_running(self, main_window: MainWindow):
        """在 RUNNING 阶段点击 Stop 应停止 calibration_service"""
        main_window.state.status = AppStatus.RUNNING
        main_window.runtime_service.stop = MagicMock()
        main_window.calibration_service.stop = MagicMock()

        main_window._stop_calibration()

        main_window.calibration_service.stop.assert_called_once()
        main_window.runtime_service.stop.assert_not_called()
