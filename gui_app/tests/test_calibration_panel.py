from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QHBoxLayout, QVBoxLayout

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

        assert panel.cm_version_combo.currentText() == "Select CM version"
        assert panel.cm_install_path is None

    def test_calibration_panel_does_not_force_large_minimum_width(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)

        assert panel.minimumSizeHint().width() < 400

    def test_phase_label_hidden_by_default(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        assert panel.phase_label.isHidden() is True

    def test_uses_campaign_and_controls_flow_without_runtime_status_group(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)

        assert panel.title() == "Calibration"
        assert panel.strategy_group.title() == "Campaign Rounds"
        assert panel.control_group.title() == "Run Controls"
        assert not hasattr(panel, "status_group")
        assert not hasattr(panel, "failure_summary")
        layout = panel.layout()
        assert layout.itemAt(0).widget() is panel.strategy_group
        assert layout.itemAt(1).widget() is panel.control_group
        assert panel.start_button.isDefault() is True

        strategy_layout = panel.strategy_group.layout()
        assert isinstance(strategy_layout, QVBoxLayout)
        estimate_row = strategy_layout.itemAt(strategy_layout.count() - 1).layout()
        assert isinstance(estimate_row, QHBoxLayout)
        assert estimate_row.itemAt(0).widget().text() == "Estimated Time"
        assert estimate_row.itemAt(1).widget() is panel.estimate_label

        control_layout = panel.control_group.layout()
        assert isinstance(control_layout, QVBoxLayout)
        status_row = control_layout.itemAt(0).layout()
        assert isinstance(status_row, QHBoxLayout)
        assert status_row.itemAt(0).widget().text() == "Status"
        assert status_row.itemAt(1).widget() is panel.status_label
        phase_row = control_layout.itemAt(1).widget()
        assert isinstance(phase_row, QLabel)
        assert phase_row is panel.phase_label
        assert control_layout.itemAt(2).widget() is panel.status_query_button

    def test_outer_title_is_stronger_than_inner_sections(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)

        panel_style = panel.styleSheet()
        section_style = panel.strategy_group.styleSheet()

        assert "font-weight: 700" in panel_style
        assert "font-weight: 600" in section_style

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
    def test_uses_linear_project_camera_and_results_flow_in_english(self, qtbot):
        panel = CmSettingsPanel()
        qtbot.addWidget(panel)

        assert panel.title() == "CM Settings"
        assert panel.browse_button.text() == "Browse"
        assert panel.precheck_button.text() == "Check Inputs"
        assert panel.generate_config_button.text() == "Generate Configs"
        assert panel.project_group.title() == "Project Inputs"
        assert panel.camera_group.title() == "Camera Selection"
        assert panel.results_group.title() == "Check Results"

    def test_outer_title_is_stronger_than_inner_sections(self, qtbot):
        panel = CmSettingsPanel()
        qtbot.addWidget(panel)

        assert "font-weight: 700" in panel.styleSheet()
        assert "font-weight: 600" in panel.project_group.styleSheet()

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
    def test_uses_single_progress_box_with_english_title(self, qtbot):
        panel = SensorProgressPanel()
        qtbot.addWidget(panel)

        assert panel.title() == "Sensor Progress"
        layout = panel.layout()
        assert layout.count() == 4
        assert layout.itemAt(0).widget() is panel.current_sensor_label
        assert layout.itemAt(1).widget() is panel.overall_progress_bar
        assert layout.itemAt(2).widget() is panel.overall_progress_detail_label
        assert layout.itemAt(3).widget() is panel.sensor_progress_tree
        assert not hasattr(panel, "summary_group")
        assert not hasattr(panel, "detail_group")
        assert "font-weight: 700" in panel.styleSheet()

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
