from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import PropertyMock

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
        """预检通过且运行态 ready 时应直接启动标定，而不是再次 prepare"""
        message_box = mocker.patch("gui_app.main_window.QMessageBox")
        from pathlib import Path
        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window.precheck_service.run_for_cameras = MagicMock(return_value=[
            {"camera": "cam1", "ok": True, "message": "ok"}
        ])
        main_window.runtime_service.prepare_runtime = MagicMock()
        main_window.calibration_service.start = MagicMock()
        main_window.state.status = AppStatus.READY
        main_window._last_runtime_summary = {
            "status": "ready",
            "running_projectdir": str(main_window.project_root),
            "process_counts": {"carmaker": 2, "carmaker_runtime": 1, "carmaker_gui": 1, "gui_movie": 1, "gpusensor_movie": 1},
            "active_sensors": ["cam1"],
            "health": {"code": "ok"},
        }

        main_window._start_calibration()

        main_window.runtime_service.prepare_runtime.assert_not_called()
        main_window.calibration_service.start.assert_called_once()
        launch = main_window.calibration_service.start.call_args.args[0]
        assert launch.skip_prepare_for_first_camera is True
        message_box.warning.assert_not_called()

    def test_start_calibration_when_runtime_not_ready_prompts_prepare(self, main_window: MainWindow, qtbot, mocker):
        """运行态未 ready 时应提示用户先点击 CM Prepare"""
        message_box = mocker.patch("gui_app.main_window.QMessageBox")
        from pathlib import Path
        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window.precheck_service.run_for_cameras = MagicMock(return_value=[
            {"camera": "cam1", "ok": True, "message": "ok"}
        ])
        main_window.calibration_service.start = MagicMock()
        main_window.runtime_service.prepare_runtime = MagicMock()
        main_window.state.status = AppStatus.PASSIVE
        main_window._last_runtime_summary = {
            "status": "passive",
            "status_reason": "runtime not ready",
            "running_projectdir": str(main_window.project_root),
            "process_counts": {"carmaker": 0, "carmaker_runtime": 0, "carmaker_gui": 0, "gui_movie": 0, "gpusensor_movie": 0},
            "active_sensors": [],
            "health": None,
        }

        main_window._start_calibration()

        main_window.calibration_service.start.assert_not_called()
        main_window.runtime_service.prepare_runtime.assert_not_called()
        feedback = main_window.calibration_panel.failure_summary.toPlainText()
        assert "环境状态未知，请点击 CM Prepare 准备环境。" in feedback
        message_box.warning.assert_called_once()

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

    def test_runtime_summary_prepare_ready_updates_feedback_box(self, main_window: MainWindow, mocker):
        """手动 prepare 成功时，中栏底部文本框应显示成功摘要"""
        mocker.patch("gui_app.main_window.QMessageBox")

        main_window._on_runtime_summary({
            "mode": "prepare",
            "status": "ready",
            "status_reason": "runtime ready",
            "testrun_control": "Tcl StartSim/StopSim",
            "active_sensors": ["cam1"],
            "process_counts": {"carmaker": 2, "carmaker_gui": 1, "carmaker_runtime": 1, "gui_movie": 1, "gpusensor_movie": 1},
        })

        summary_text = main_window.calibration_panel.failure_summary.toPlainText()
        assert "CM Prepare 成功" in summary_text
        assert "Active sensors: cam1" in summary_text

    def test_runtime_summary_prepare_passive_updates_feedback_box(self, main_window: MainWindow, mocker):
        """prepare 未就绪时，中栏底部文本框应显示失败原因"""
        mocker.patch("gui_app.main_window.QMessageBox")

        main_window._on_runtime_summary({
            "mode": "prepare",
            "status": "passive",
            "status_reason": "expected exactly 1 CarMaker backend runtime, found 2",
            "testrun_control": "Tcl StartSim/StopSim",
            "active_sensors": ["cam1"],
            "process_counts": {"carmaker": 3, "carmaker_gui": 1, "carmaker_runtime": 2, "gui_movie": 1, "gpusensor_movie": 1},
        })

        summary_text = main_window.calibration_panel.failure_summary.toPlainText()
        assert "CM Prepare 未达到就绪状态" in summary_text
        assert "expected exactly 1 CarMaker backend runtime, found 2" in summary_text

    def test_runtime_process_started_logs_prepare_plan(self, main_window: MainWindow):
        """点击 CM Prepare 后，右侧日志应立即显示准备流程说明"""
        main_window._runtime_mode = "prepare"
        main_window.output_panel.append_log = MagicMock()

        main_window._on_runtime_process_started()

        logged_messages = [call.args[0] for call in main_window.output_panel.append_log.call_args_list]
        assert any("CM Prepare started:" in message for message in logged_messages)
        assert any("CM Prepare steps:" in message for message in logged_messages)
        summary_text = main_window.calibration_panel.failure_summary.toPlainText()
        assert "CM Prepare 进行中..." in summary_text
        assert "Prepare steps:" in summary_text

    def test_prepare_runtime_sets_summary_box_immediately(self, main_window: MainWindow):
        """点击 CM Prepare 后，中栏摘要栏不应保持空白"""
        from pathlib import Path

        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window.runtime_service.prepare_runtime = MagicMock()

        main_window._prepare_runtime()

        summary_text = main_window.calibration_panel.failure_summary.toPlainText()
        assert "CM Prepare 已触发" in summary_text

    def test_runtime_line_prepare_logs_incremental_structured_steps(self, main_window: MainWindow):
        """prepare 期间 runtime stdout 应逐步补充结构化 Prepare 日志，同时保留原始行"""
        config_path = main_window.project_root / "Data" / "Script" / "CameraCalibration" / "configs" / "camera.cam1.json"
        main_window._runtime_mode = "prepare"
        main_window.output_panel.append_log = MagicMock()

        main_window._on_runtime_line(f"Project root: {main_window.project_root}")
        main_window._on_runtime_line("TestRun: Data/TestRun/vctc_ngxpro")
        main_window._on_runtime_line("Vehicle: Data/Vehicle/Examples/TestVehicle")
        main_window._on_runtime_line("Activated vehicle sensor: cam1 (Sensor.0.Active = 1)")
        main_window._on_runtime_line("Vehicle file already matched the requested single-sensor state")
        main_window._on_runtime_line("CarMaker action: reused existing CarMaker GUI/runtime")
        main_window._on_runtime_line("CarMaker PID: 1234")
        main_window._on_runtime_line("CarMaker GUI TestRun selected: vctc_ngxpro")
        main_window._on_runtime_line(
            "Bootstrap run: Tcl StartSim/StopSim reached running state and returned to idle for TestRun vctc_ngxpro"
        )
        main_window._on_runtime_line("IPG-MOVIE action: reused existing GUI IPG-MOVIE PID 5678")
        main_window._on_runtime_line("IPG-MOVIE PID: 5678")
        main_window._on_runtime_line(
            "IPG-MOVIE scene ready: mode=strict recovery=none camera_name=CAMERA_RSI-SENSOR Vhcl.cam1 size=1920x1536 camera_widget=.view"
        )
        main_window._on_runtime_line("IPG-MOVIE ABRAXAS: before=0 after=1")
        main_window._on_runtime_line(
            "IPG-MOVIE selected camera sensor: requested=CAMERA_RSI-SENSOR Vhcl.cam1 current=CAMERA_RSI-SENSOR Vhcl.cam1"
        )
        main_window._on_runtime_line("IPG-MOVIE camera widgets: camera=.camera lens=.lens lens_state=normal")
        main_window._on_runtime_line(
            f"IPG-MOVIE captured current initial values: config={config_path} names=pos_x, pos_y"
        )
        main_window._on_runtime_line("IPG-MOVIE health check: all_ok=True code=ok")

        logged_messages = [call.args[0] for call in main_window.output_panel.append_log.call_args_list]
        assert "CarMaker action: reused existing CarMaker GUI/runtime" in logged_messages
        assert any(
            message == (
                f"Prepare target: project={main_window.project_root} | "
                "testrun=Data/TestRun/vctc_ngxpro | vehicle=Data/Vehicle/Examples/TestVehicle"
            )
            for message in logged_messages
        )
        assert "Prepare sensor activation: cam1 (Sensor.0.Active=1, changed=no)" in logged_messages
        assert "Prepare GUI TestRun selection: vctc_ngxpro" in logged_messages
        assert "Prepare bootstrap: Tcl StartSim/StopSim -> vctc_ngxpro" in logged_messages
        assert "Prepare CarMaker: action=reused existing CarMaker GUI/runtime | pid=1234" in logged_messages
        assert "Prepare IPG-MOVIE: action=reused existing GUI IPG-MOVIE PID 5678 | pid=5678" in logged_messages
        assert (
            "Prepare IPG-MOVIE scene: mode=strict | camera=CAMERA_RSI-SENSOR Vhcl.cam1 | size=1920x1536 | view_widget=.view"
            in logged_messages
        )
        assert "Prepare ABRAXAS: before=0 | after=1" in logged_messages
        assert (
            "Prepare camera selection: requested=CAMERA_RSI-SENSOR Vhcl.cam1 | current=CAMERA_RSI-SENSOR Vhcl.cam1"
            in logged_messages
        )
        assert "Prepare camera widgets: camera=.camera | lens=.lens | lens_state=normal" in logged_messages
        assert (
            f"Prepare initial capture: config={config_path} | names=pos_x, pos_y" in logged_messages
        )
        assert "Prepare health check: all_ok=True | code=ok" in logged_messages

    def test_runtime_summary_prepare_does_not_replay_full_trace(self, main_window: MainWindow, mocker):
        """prepare summary 返回后，只保留 summary 行，不再整组补打 Prepare 明细"""
        mocker.patch("gui_app.main_window.QMessageBox")
        main_window.output_panel.append_log = MagicMock()

        main_window._on_runtime_summary({
            "mode": "prepare",
            "status": "ready",
            "project_root": str(main_window.project_root),
            "testrun": "vctc_ngxpro",
            "vehicle": "Examples/TestVehicle",
            "selected_testrun": "vctc_ngxpro",
            "testrun_control": "Tcl StartSim/StopSim",
            "testrun_bootstrap": {
                "label": "Tcl StartSim/StopSim",
                "testrun": "vctc_ngxpro",
            },
            "sensor_activation": {
                "selected_sensor_name": "cam1",
                "selected_sensor_index": 0,
                "changed": False,
            },
            "carmaker": {
                "pid": 1234,
                "action": "reused existing CarMaker GUI/runtime",
            },
            "movie": {
                "pid": 5678,
                "action": "reused existing GUI IPG-MOVIE PID 5678",
                "scene": {
                    "mode": "strict",
                    "camera_name": "CAMERA_RSI-SENSOR Vhcl.cam1",
                    "width": "1920",
                    "height": "1536",
                    "view_widget": ".view",
                },
                "abraxas": {
                    "before": "0",
                    "after": "1",
                },
                "camera_selection": {
                    "selected": "CAMERA_RSI-SENSOR Vhcl.cam1",
                    "current": "CAMERA_RSI-SENSOR Vhcl.cam1",
                },
                "camera_widgets": {
                    "after_camera": ".camera",
                    "after_lens": ".lens",
                    "lens_state": "normal",
                },
                "camera_dialogs": {
                    "camera_state": "normal",
                    "lens_state": "normal",
                },
            },
            "config_initial_capture": {
                "config_path": str(main_window.project_root / "Data" / "Script" / "CameraCalibration" / "configs" / "camera.cam1.json"),
                "captured_names": ["pos_x", "pos_y"],
            },
            "health": {
                "code": "ok",
                "message": "remote control healthy",
            },
            "active_sensors": ["cam1"],
            "process_counts": {"carmaker": 2, "carmaker_gui": 1, "carmaker_runtime": 1, "gui_movie": 1, "gpusensor_movie": 1},
        })

        logged_messages = [call.args[0] for call in main_window.output_panel.append_log.call_args_list]
        assert any(message.startswith("summary mode=prepare status=ready") for message in logged_messages)
        assert not any(message.startswith("Prepare ") for message in logged_messages)

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
        progress_bar = main_window.sensor_progress_panel.sensor_progress_tree.itemWidget(item, 2)
        assert item.text(1) == "running"
        assert progress_bar.value() >= 0

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

    def test_health_probe_does_not_disable_start_when_ready(self, main_window: MainWindow, mocker):
        """后台健康轮询进行中时，ready 状态下 Start 不应被临时锁死"""
        mocker.patch.object(type(main_window.runtime_service), "is_running", new_callable=PropertyMock, return_value=True)
        main_window._health_check_active = True
        main_window._apply_status(AppStatus.READY)

        assert main_window.calibration_panel.start_button.isEnabled() is True

    def test_ready_status_periodic_health_probe_uses_current_cm_install(self, main_window: MainWindow):
        """Status=ready 时，周期性轮询应携带当前选中的 CM 版本"""
        from pathlib import Path

        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window.runtime_service.probe_status = MagicMock()
        main_window._apply_status(AppStatus.READY)

        main_window._check_runtime_health()

        main_window.runtime_service.probe_status.assert_called_once_with(
            main_window.project_root,
            "vctc_ngxpro",
            verify_health=True,
            cm_install=Path("D:/cm/win64-test"),
        )

    def test_status_query_button_only_enabled_for_manual_query_states(self, main_window: MainWindow):
        """手动状态查询按钮只应在 passive/finished/failed/stopped 时可用"""
        main_window._apply_status(AppStatus.PASSIVE)
        assert main_window.calibration_panel.status_query_button.isEnabled() is True

        main_window._apply_status(AppStatus.FAILED)
        assert main_window.calibration_panel.status_query_button.isEnabled() is True

        main_window._apply_status(AppStatus.READY)
        assert main_window.calibration_panel.status_query_button.isEnabled() is False

        main_window._apply_status(AppStatus.RUNNING)
        assert main_window.calibration_panel.status_query_button.isEnabled() is False

    def test_manual_status_query_uses_current_cm_install(self, main_window: MainWindow):
        """手动状态查询应使用当前选中的 CM 版本"""
        from pathlib import Path

        main_window.calibration_panel.cm_version_combo.clear()
        main_window.calibration_panel.cm_version_combo.addItem("test", Path("D:/cm/win64-test"))
        main_window.runtime_service.probe_status = MagicMock()
        main_window._apply_status(AppStatus.PASSIVE)

        main_window._query_runtime_status()

        main_window.runtime_service.probe_status.assert_called_once_with(
            main_window.project_root,
            "vctc_ngxpro",
            verify_health=True,
            cm_install=Path("D:/cm/win64-test"),
        )

    def test_status_probe_does_not_override_running_status(self, main_window: MainWindow, mocker):
        """标定 running 期间，晚到的 status 轮询结果不应把 Status 拉回 ready/passive"""
        mocker.patch("gui_app.main_window.QMessageBox")
        main_window._apply_status(AppStatus.RUNNING)

        main_window._on_runtime_summary({
            "mode": "status",
            "status": "ready",
            "status_reason": "runtime ready",
            "running_projectdir": str(main_window.project_root),
            "process_counts": {"carmaker": 2, "carmaker_runtime": 1, "carmaker_gui": 1, "gui_movie": 1, "gpusensor_movie": 1},
            "active_sensors": ["cam1"],
            "health": {"code": "ok"},
        })

        assert main_window.state.status == AppStatus.RUNNING
        assert main_window.calibration_panel.status_label.text() == "running"
        summary_text = main_window.calibration_panel.failure_summary.toPlainText()
        assert "保持当前 Status=running" in summary_text

    def test_running_status_skips_periodic_health_probe(self, main_window: MainWindow):
        """Status=running 时不应继续发起 runtime health probe"""
        main_window.runtime_service.probe_status = MagicMock()
        main_window._apply_status(AppStatus.RUNNING)

        main_window._check_runtime_health()

        main_window.runtime_service.probe_status.assert_not_called()
