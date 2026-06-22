from __future__ import annotations

from unittest.mock import MagicMock

from src.gui_app.main_window import MainWindow
from src.gui_app.models.state import AppStatus
from src.gui_app.widgets.output_panel import (
    BEST_IMAGE_ROLE,
    BEST_OVERLAY_IMAGE_ROLE,
    BEST_SCORE_IMAGE_ROLE,
    CURRENT_ITER_IMAGE_ROLE,
)


class TestCalibStartFlow:
    """验证标定启动流程：预检 → 启动"""

    def test_main_window_starts_with_empty_status_summary(self, main_window: MainWindow):
        """主窗初始化后，不应残留旧的状态摘要文本"""
        assert list(main_window._status_summary_lines) == []

    def test_start_calibration_precheck_fails(self, main_window: MainWindow, qtbot, mocker):
        """预检失败时不应调用 calibration"""
        mocker.patch("gui_app.main_window.QMessageBox")
        main_window.precheck_service.run_for_cameras = MagicMock(return_value=[
            {"camera": "cam1", "ok": False, "message": "missing movie file"}
        ])
        main_window.calibration_service.start = MagicMock()

        main_window._start_calibration()

        main_window.calibration_service.start.assert_not_called()

    def test_generate_configs_failure_records_status_summary(self, main_window: MainWindow, mocker):
        """Generate Configs 失败时，应通过弹窗和日志暴露错误"""
        message_box = mocker.patch("gui_app.main_window.QMessageBox")
        main_window.output_panel.append_log = MagicMock()
        main_window.precheck_service.generate_configs_for_cameras = MagicMock(side_effect=RuntimeError("boom"))
        main_window.calibration_panel.clear_failure_summary()

        main_window.precheck_service.run_for_cameras = MagicMock(return_value=[
            {"camera": "cam1", "ok": True, "message": "ok"}
        ])

        main_window._generate_configs()

        message_box.critical.assert_called_once()
        assert "boom" in message_box.critical.call_args.args[2]
        logged_messages = [call.args[0] for call in main_window.output_panel.append_log.call_args_list]
        assert any("Generate configs failed: boom" in message for message in logged_messages)

    def test_orchestration_events_update_sensor_progress_display(self, main_window: MainWindow, mocker):
        """编排事件应推动左侧传感器进度显示"""
        mocker.patch("gui_app.main_window.QMessageBox")
        from pathlib import Path

        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window._build_launch_config()
        main_window._on_process_started()

        main_window._on_orchestration_event({"event": "camera_prepare_started", "camera": "cam1"})
        main_window._on_orchestration_event({"event": "camera_run_started", "camera": "cam1"})

        assert main_window.sensor_progress_panel.current_sensor_label.text() == "Current Sensor: cam1"
        assert main_window.sensor_progress_panel.sensor_progress_tree.topLevelItemCount() == 1
        item = main_window.sensor_progress_panel.sensor_progress_tree.topLevelItem(0)
        progress_bar = main_window.sensor_progress_panel.sensor_progress_tree.itemWidget(item, 4)
        assert item.text(1) == "running"
        assert progress_bar.value() >= 0

    def test_camera_run_progress_keeps_task_level_global_best(self, main_window: MainWindow, mocker):
        """运行中多次 progress 时，best 相关展示应保持整次任务全局最优"""
        mocker.patch("gui_app.main_window.QMessageBox")
        from pathlib import Path

        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window._build_launch_config()
        main_window._on_process_started()
        main_window._on_orchestration_event({"event": "task_started", "output_dir": str(main_window.project_root / "out")})
        main_window._on_orchestration_event({"event": "camera_run_started", "camera": "cam1"})

        main_window._on_orchestration_event(
            {
                "event": "camera_run_progress",
                "camera": "cam1",
                "progress": {
                    "best_score": 59.0,
                    "best_image": r"C:\best_a.png",
                    "best_score_image": r"C:\best_a_score.png",
                    "best_overlay_image": r"C:\best_a_overlay.png",
                    "current_iter_score": 59.0,
                    "current_iter_index": 1,
                    "current_iter_image": r"C:\iter_1.png",
                },
            }
        )
        main_window._on_orchestration_event(
            {
                "event": "camera_run_progress",
                "camera": "cam1",
                "progress": {
                    "best_score": 69.0,
                    "best_image": r"C:\best_b.png",
                    "best_score_image": r"C:\best_b_score.png",
                    "best_overlay_image": r"C:\best_b_overlay.png",
                    "current_iter_score": 69.0,
                    "current_iter_index": 7,
                    "current_iter_image": r"C:\iter_7.png",
                },
            }
        )

        item = main_window.output_panel.result_tree.topLevelItem(0)
        assert item is not None
        assert item.text(0) == "cam1"
        assert item.text(2) == "59.00"
        assert item.data(0, BEST_IMAGE_ROLE) == r"C:\best_a.png"
        assert item.data(0, BEST_SCORE_IMAGE_ROLE) == r"C:\best_a_score.png"
        assert item.data(0, BEST_OVERLAY_IMAGE_ROLE) == r"C:\best_a_overlay.png"
        assert item.data(0, CURRENT_ITER_IMAGE_ROLE) == r"C:\iter_7.png"
        assert item.text(3) == "69.00"

        card = main_window.output_panel._result_cards["cam1"]
        assert card.best_score_value.text() == "59.00"
        assert card.score_preview._artifact_path == r"C:\best_a_score.png"
        assert card.overlay_preview._artifact_path == r"C:\best_a_overlay.png"
        assert card.open_best_button.isEnabled() is True

    def test_camera_run_progress_merges_equal_score_artifacts(self, main_window: MainWindow, mocker):
        """同分 progress 应允许补齐先前缺失的 best artifact"""
        mocker.patch("gui_app.main_window.QMessageBox")
        from pathlib import Path

        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window._build_launch_config()
        main_window._on_process_started()
        main_window._on_orchestration_event({"event": "task_started", "output_dir": str(main_window.project_root / "out")})
        main_window._on_orchestration_event({"event": "camera_run_started", "camera": "cam1"})

        main_window._on_orchestration_event(
            {
                "event": "camera_run_progress",
                "camera": "cam1",
                "progress": {
                    "best_score": 59.0,
                    "best_image": r"C:\best_a.png",
                    "best_overlay_image": r"C:\best_a_overlay.png",
                    "current_iter_score": 59.0,
                    "current_iter_index": 1,
                    "current_iter_image": r"C:\iter_1.png",
                },
            }
        )
        main_window._on_orchestration_event(
            {
                "event": "camera_run_progress",
                "camera": "cam1",
                "progress": {
                    "best_score": 59.0,
                    "best_image": r"C:\best_a.png",
                    "best_score_image": r"C:\best_a_score.png",
                    "best_overlay_image": r"C:\best_a_overlay.png",
                    "current_iter_score": 58.5,
                    "current_iter_index": 2,
                    "current_iter_image": r"C:\iter_2.png",
                },
            }
        )

        item = main_window.output_panel.result_tree.topLevelItem(0)
        assert item is not None
        assert item.text(2) == "59.00"
        assert item.data(0, BEST_SCORE_IMAGE_ROLE) == r"C:\best_a_score.png"

        card = main_window.output_panel._result_cards["cam1"]
        assert card.score_preview._artifact_path == r"C:\best_a_score.png"
        assert card.overlay_preview._artifact_path == r"C:\best_a_overlay.png"

    def test_camera_run_finished_keeps_last_current_iter_frame(self, main_window: MainWindow, mocker):
        """单相机运行结束后 current iter 预览应保留最后一帧"""
        mocker.patch("gui_app.main_window.QMessageBox")
        from pathlib import Path

        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window._build_launch_config()
        main_window._on_process_started()
        main_window._on_orchestration_event({"event": "task_started", "output_dir": str(main_window.project_root / "out")})
        main_window._on_orchestration_event({"event": "camera_run_started", "camera": "cam1"})

        main_window._on_orchestration_event(
            {
                "event": "camera_run_progress",
                "camera": "cam1",
                "progress": {
                    "best_score": 59.0,
                    "best_image": r"C:\best_a.png",
                    "best_score_image": r"C:\best_a_score.png",
                    "best_overlay_image": r"C:\best_a_overlay.png",
                    "current_iter_score": 61.0,
                    "current_iter_index": 7,
                    "current_iter_image": r"C:\iter_7.png",
                },
            }
        )
        main_window._on_orchestration_event({"event": "camera_run_finished", "camera": "cam1"})

        item = main_window.output_panel.result_tree.topLevelItem(0)
        assert item is not None
        assert item.data(0, CURRENT_ITER_IMAGE_ROLE) == r"C:\iter_7.png"

        card = main_window.output_panel._result_cards["cam1"]
        assert card.iter_preview._artifact_path == r"C:\iter_7.png"

    def test_orchestration_summary_keeps_last_current_iter_frame(self, main_window: MainWindow, mocker):
        """最终 summary 未携带 current_iter_image 时，也应保留最后一帧"""
        mocker.patch("gui_app.main_window.QMessageBox")
        from pathlib import Path

        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window._build_launch_config()
        main_window._on_process_started()
        main_window._on_orchestration_event({"event": "task_started", "output_dir": str(main_window.project_root / "out")})
        main_window._on_orchestration_event({"event": "camera_run_started", "camera": "cam1"})
        main_window._on_orchestration_event(
            {
                "event": "camera_run_progress",
                "camera": "cam1",
                "progress": {
                    "best_score": 59.0,
                    "best_image": r"C:\best_a.png",
                    "best_score_image": r"C:\best_a_score.png",
                    "best_overlay_image": r"C:\best_a_overlay.png",
                    "current_iter_score": 61.0,
                    "current_iter_index": 7,
                    "current_iter_image": r"C:\iter_7.png",
                },
            }
        )

        main_window._on_orchestration_summary(
            {
                "status": "finished",
                "per_camera": [
                    {
                        "camera": "cam1",
                        "status": "finished",
                        "calibration": {
                            "best_score": 59.0,
                            "current_iter_score": 61.0,
                            "current_iter_index": 7,
                            "best_image": r"C:\best_a.png",
                            "best_score_image": r"C:\best_a_score.png",
                            "best_overlay_image": r"C:\best_a_overlay.png",
                        },
                    }
                ],
            }
        )

        item = main_window.output_panel.result_tree.topLevelItem(0)
        assert item is not None
        assert item.data(0, CURRENT_ITER_IMAGE_ROLE) == r"C:\iter_7.png"

        card = main_window.output_panel._result_cards["cam1"]
        assert card.iter_preview._artifact_path == r"C:\iter_7.png"

    def test_stop_calibration_during_running(self, main_window: MainWindow):
        """在 RUNNING 阶段点击 Stop 应停止 calibration_service"""
        main_window.state.status = AppStatus.RUNNING
        main_window.runtime_service.stop = MagicMock()
        main_window.calibration_service.stop = MagicMock()

        main_window._stop_calibration()

        main_window.calibration_service.stop.assert_called_once()
        main_window.runtime_service.stop.assert_not_called()
