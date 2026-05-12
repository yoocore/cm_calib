from __future__ import annotations

from gui_app.widgets.calibration_panel import CalibrationPanel


class TestCalibrationPanel:
    def test_phase_label_default_empty(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        assert panel.phase_label.text() == ""

    def test_set_phase_label_shows_text(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        panel.set_phase_label("test phase")
        assert panel.phase_label.text() == "test phase"

    def test_set_phase_label_clears_with_none(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        panel.set_phase_label("something")
        panel.set_phase_label(None)
        assert panel.phase_label.text() == ""

    def test_estimated_time_zero_cameras(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        assert "0s" in panel.estimate_label.text()

    def test_update_precheck_results_shows_checkmarks(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        panel.update_precheck_results([
            {"camera": "cam1", "ok": True, "message": "ok"},
            {"camera": "cam2", "ok": False, "message": "missing file"},
        ])
        assert panel.precheck_tree.topLevelItemCount() == 2
        item_ok = panel.precheck_tree.topLevelItem(0)
        item_fail = panel.precheck_tree.topLevelItem(1)
        assert item_ok.text(1) == "✓"
        assert item_fail.text(1) == "✗"

    def test_generate_configs_ready_after_all_ok(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        panel.update_precheck_results([
            {"camera": "cam1", "ok": True, "message": "ok"},
        ])
        assert panel._generate_configs_ready is True
        assert panel.generate_config_button.isEnabled() is True

    def test_generate_configs_not_ready_after_any_fail(self, qtbot):
        panel = CalibrationPanel()
        qtbot.addWidget(panel)
        panel.update_precheck_results([
            {"camera": "cam1", "ok": False, "message": "fail"},
        ])
        assert panel._generate_configs_ready is False
        assert panel.generate_config_button.isEnabled() is False
