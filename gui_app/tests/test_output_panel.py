from __future__ import annotations

import os
import re

from gui_app.models.state import CameraResult
from gui_app.widgets.output_panel import OutputPanel, _classify_log_level, _normalize_log_source


class TestOutputPanel:
    def test_output_panel_uses_clear_results_and_logs_labels(self, qtbot):
        panel = OutputPanel()
        qtbot.addWidget(panel)

        assert panel.title() == "结果与日志"
        assert panel.open_output_button.text() == "打开输出"
        assert panel.open_log_button.text() == "打开日志"

        card = panel._ensure_result_card("cam1")
        assert card.open_result_button.text() == "结果"
        assert card.open_current_button.text() == "当前帧"
        assert card.open_overlay_button.text() == "叠加"

    def test_output_panel_has_no_bottom_preview_area(self, qtbot):
        panel = OutputPanel()
        qtbot.addWidget(panel)

        assert not hasattr(panel, "preview_image_label")
        assert not hasattr(panel, "open_result_button")

    def test_normalize_log_source_prefers_explicit_source(self):
        source, message = _normalize_log_source("runtime", "[calibration] failed")
        assert source == "runtime"
        assert message == "[calibration] failed"

    def test_normalize_log_source_extracts_prefixed_source(self):
        source, message = _normalize_log_source(None, "[runtime] summary status=ready")
        assert source == "runtime"
        assert message == "summary status=ready"

    def test_classify_log_level(self):
        assert _classify_log_level("everything completed and ready") == "success"
        assert _classify_log_level("warning: timed out while probing") == "warning"
        assert _classify_log_level("Traceback: RuntimeError") == "error"
        assert _classify_log_level("plain diagnostic line") == "info"

    def test_append_log_adds_timestamp_source_and_level(self, qtbot):
        panel = OutputPanel()
        qtbot.addWidget(panel)

        panel.append_log("summary status=ready", source="runtime")
        text = panel.log_view.toPlainText().strip()

        assert re.search(r"^\[\d{2}:\d{2}:\d{2}\] \[RUNTIME\] \[SUCCESS\] summary status=ready$", text)

    def test_append_log_styles_warning_and_error(self, qtbot):
        panel = OutputPanel()
        qtbot.addWidget(panel)

        panel.append_log("warning: runtime not ready", source="runtime")
        panel.append_log("error: calibration failed", source="calibration")
        html = panel.log_view.toHtml()

        assert "[WARNING]" in html
        assert "[ERROR]" not in html
        assert "#ffd54f" in html

    def test_current_log_path_falls_back_to_task_log(self, qtbot):
        panel = OutputPanel()
        qtbot.addWidget(panel)

        panel.set_log_path(r"C:\logs\events.jsonl")
        panel.update_camera_result(CameraResult(camera="cam1", status="running"))

        assert panel.current_log_path() == r"C:\logs\events.jsonl"
        assert panel.open_log_button.isEnabled() is True

    def test_open_log_file_uses_task_log_fallback(self, qtbot, mocker):
        panel = OutputPanel()
        qtbot.addWidget(panel)

        panel.set_log_path(r"C:\logs\events.jsonl")
        panel.update_camera_result(CameraResult(camera="cam1", status="running"))
        startfile = mocker.patch.object(os, "startfile")

        panel._open_log_file()

        startfile.assert_called_once_with(r"C:\logs\events.jsonl")

    def test_best_score_preview_clears_when_real_score_image_is_missing(self, qtbot):
        panel = OutputPanel()
        qtbot.addWidget(panel)

        panel.update_camera_result(
            CameraResult(
                camera="cam1",
                status="running",
                best_score=59.0,
                best_score_image=r"C:\artifacts\best_score.png",
            )
        )
        panel.update_camera_result(
            CameraResult(
                camera="cam1",
                status="running",
                best_score=59.0,
                best_image=r"C:\artifacts\best.png",
                result_json=r"C:\artifacts\result.json",
            )
        )

        card = panel._result_cards["cam1"]
        assert card.score_preview._artifact_path is None
        assert card.open_score_button.isEnabled() is False

    def test_best_score_column_does_not_fallback_to_other_artifacts(self, qtbot):
        panel = OutputPanel()
        qtbot.addWidget(panel)

        panel.update_camera_result(
            CameraResult(
                camera="cam1",
                status="running",
                best_score=59.0,
                best_image=r"C:\artifacts\best.png",
                result_json=r"C:\artifacts\result.json",
            )
        )

        item = panel.result_tree.topLevelItem(0)
        assert item is not None
        assert panel.resolve_item_artifact(item, 2) is None
