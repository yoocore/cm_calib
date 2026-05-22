from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt

from gui_app.widgets.calibration_panel import CalibrationPanel
from gui_app.widgets.cm_settings_panel import CmSettingsPanel
from gui_app.widgets.sensor_progress_panel import SensorProgressPanel


class TestCalibrationPanel:
    def test_cm_version_defaults_to_empty_selection(self, qtbot, mocker):
        mocker.patch(
            "gui_app.widgets.calibration_panel.detect_cm_versions",
            return_value={"14.1": Path("D:/IPG/carmaker/win64-14.1")},
        )
        panel = CalibrationPanel()
        qtbot.addWidget(panel)

        assert panel.cm_version_combo.currentText() == "请选择 CM 版本"
        assert panel.cm_install_path is None

    def test_phase_label_hidden_but_summary_stays_visible(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        assert panel.phase_label.isHidden() is True
        assert panel.failure_summary.isHidden() is False

    def test_uses_dedicated_status_strategy_and_control_sections(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)

        assert panel.status_group.title() == "运行状态"
        assert panel.strategy_group.title() == "优化策略"
        assert panel.control_group.title() == "运行控制"
        assert panel.start_button.isDefault() is True

    def test_status_badge_default_idle(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        assert panel.status_label.text() == "idle"
        stylesheet = panel.status_label.styleSheet()
        assert "border: 2px solid" in stylesheet
        assert "font-weight: 700" in stylesheet

    def test_set_status_updates_badge_style(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        panel.set_status("ready")
        assert panel.status_label.text() == "ready"
        ready_stylesheet = panel.status_label.styleSheet()
        assert "#2e7d32" in ready_stylesheet
        panel.set_status("failed")
        assert panel.status_label.text() == "fail"
        failed_stylesheet = panel.status_label.styleSheet()
        assert "#c62828" in failed_stylesheet
        assert failed_stylesheet != ready_stylesheet

    def test_estimated_time_shows_per_camera(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        assert "/ camera" in panel.estimate_label.text()


class TestCmSettingsPanel:
    def test_uses_project_camera_and_result_sections(self, qtbot):
        panel = CmSettingsPanel()
        qtbot.addWidget(panel)

        assert panel.project_group.title() == "工程输入"
        assert panel.camera_group.title() == "相机选择"
        assert panel.results_group.title() == "检查结果"

    def test_update_precheck_results_shows_checkmarks(self, qtbot):
        panel = CmSettingsPanel()
        qtbot.addWidget(panel)
        panel.update_precheck_results([
            {"camera": "cam1", "ok": True, "message": "ok"},
            {"camera": "cam2", "ok": False, "message": "missing file"},
        ])
        assert panel.precheck_tree.topLevelItemCount() == 2
        item_ok = panel.precheck_tree.topLevelItem(0)
        item_fail = panel.precheck_tree.topLevelItem(1)
        assert item_ok.text(1) == "✓"
        assert item_fail.text(1) == "✗ missing file"

    def test_generate_configs_ready_after_all_ok(self, qtbot):
        panel = CmSettingsPanel()
        qtbot.addWidget(panel)
        panel.update_precheck_results([
            {"camera": "cam1", "ok": True, "message": "ok"},
        ])
        assert panel._generate_configs_ready is True
        assert panel.generate_config_button.isEnabled() is True

    def test_generate_configs_not_ready_after_any_fail(self, qtbot):
        panel = CmSettingsPanel()
        qtbot.addWidget(panel)
        panel.update_precheck_results([
            {"camera": "cam1", "ok": False, "message": "fail"},
        ])
        assert panel._generate_configs_ready is False
        assert panel.generate_config_button.isEnabled() is False


class TestSensorProgressPanel:
    def test_uses_summary_and_detail_sections(self, qtbot):
        panel = SensorProgressPanel()
        qtbot.addWidget(panel)

        assert panel.summary_group.title() == "总体进度"
        assert panel.detail_group.title() == "相机明细"

    def test_sensor_progress_plan_and_runtime_update(self, qtbot):
        panel = SensorProgressPanel()
        qtbot.addWidget(panel)

        panel.reset_sensor_progress(
            cameras=["cam1", "cam2"],
            estimated_per_camera=200,
            estimated_total=400,
        )

        assert panel.sensor_progress_tree.topLevelItemCount() == 2

        panel.set_sensor_progress(
            "cam1",
            status="running",
            progress_percent=42,
            elapsed_seconds=84,
            estimated_seconds=200,
            detail="iter=12",
        )
        panel.set_overall_progress(
            current_camera="cam1",
            completed_count=0,
            total_count=2,
            progress_percent=21,
            elapsed_seconds=84,
            estimated_total_seconds=400,
        )

        item = panel.sensor_progress_tree.topLevelItem(0)
        progress_bar = panel.sensor_progress_tree.itemWidget(item, 2)
        assert item.text(1) == "running"
        assert progress_bar.value() == 42
        assert "iter=12" in item.text(3)
        assert panel.current_sensor_label.text() == "Current Sensor: cam1"
