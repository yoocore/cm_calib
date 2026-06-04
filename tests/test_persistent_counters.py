"""Tests for persistent counter and trial log functionality."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Mock DDE and Windows-specific modules before importing camera_calibration
sys_modules_backup = {}
for mod in ["win32ui", "dde", "msvcrt", "pywinauto"]:
    if mod in sys.modules:
        sys_modules_backup[mod] = sys.modules[mod]
    sys.modules[mod] = MagicMock()

# Mock cv2 and numpy if not available
try:
    import cv2
except ImportError:
    sys.modules["cv2"] = MagicMock()
try:
    import numpy as np
except ImportError:
    sys.modules["numpy"] = MagicMock()

from camera_calibration import BoardScoreDetail, CameraCalibrator, TotalScoreDetail


# Restore original modules
for mod, original in sys_modules_backup.items():
    if original is not None:
        sys.modules[mod] = original
    else:
        del sys.modules[mod]


def _make_minimal_cfg(output_dir: Path) -> dict:
    """Create a minimal config dict for CameraCalibrator initialization."""
    real_image = output_dir / "real.png"
    # Create a dummy image file
    import cv2
    import numpy as np
    dummy = np.zeros((100, 100), dtype=np.uint8)
    cv2.imwrite(str(real_image), dummy)

    return {
        "real_image": str(real_image),
        "parameters": {
            "pos_x": {
                "initial": 0.0,
                "step": 1.0,
                "min_step": 0.1,
                "decimals": 3,
            }
        },
        "boards": [
            {
                "board_id": "board_1",
                "board_type": "checkerboard",
                "weight": 1.0,
                "critical": False,
                "fail_penalty": 10.0,
                "roi": [10, 10, 80, 80],
                "board_size": [9, 6],
                "square_size": 0.025,
            }
        ],
        "output_dir": str(output_dir),
        "max_iters": 10,
        "target_score": 0.1,
        "min_improve": 1e-4,
        "step_decay": 0.6,
        "settings_input_mode": "script_control",
        "comparison_mode": "direct",
        "progress_flush_every": 1,
        "max_history_entries": 500,
        "optimizer_mode": "coordinate_descent",
    }


def _make_total_detail(score: float, *, board_id: str = "board_1") -> TotalScoreDetail:
    return TotalScoreDetail(
        success=True,
        total_score=score,
        raw_total_score=score,
        degrade_penalty=0.0,
        has_critical_degrade=False,
        degraded_boards=[],
        isolated_outlier_boards=[],
        compared_board_count=1,
        board_scores=[
            BoardScoreDetail(
                board_id=board_id,
                board_type="checkerboard",
                success=True,
                compared=True,
                reference_visible=True,
                sim_visible=True,
                total_score=score,
                rmse=score,
                mean_error=score,
                max_error=score,
                miss_rate=0.0,
                matched_point_count=54,
            )
        ],
    )


class TestCounterInitialization:
    """Test that counters are properly initialized."""

    def test_trial_count_starts_at_zero(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)
        assert calib._total_trial_count == 0

    def test_iteration_count_starts_at_zero(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)
        assert calib._total_iteration_count == 0

    def test_trial_log_path_is_set(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)
        assert calib._trial_log_path == tmp_path / "trial_log.jsonl"


class TestAppendTrialLog:
    """Test the _append_trial_log method."""

    def test_appends_single_line_json(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        calib._append_trial_log(
            iteration=1,
            score=12.345,
            accepted=True,
            phase="single",
            param_name="pos_x",
        )

        log_path = calib._trial_log_path
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8").strip()
        lines = content.split("\n")
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["iteration"] == 1
        assert record["score"] == 12.345
        assert record["accepted"] is True
        assert record["phase"] == "single"
        assert record["param_name"] == "pos_x"
        assert "timestamp" in record

    def test_appends_multiple_lines(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        calib._append_trial_log(iteration=1, score=10.0, accepted=True, phase="single")
        calib._append_trial_log(iteration=2, score=9.0, accepted=True, phase="joint")
        calib._append_trial_log(iteration=3, score=float("inf"), accepted=False, phase="error")

        log_path = calib._trial_log_path
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

        records = [json.loads(line) for line in lines]
        assert records[0]["iteration"] == 1
        assert records[1]["iteration"] == 2
        assert records[2]["iteration"] == 3

    def test_omits_none_fields(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        calib._append_trial_log(
            iteration=1,
            score=10.0,
            accepted=True,
            phase="single",
            param_name="pos_x",
            direction=None,
            trial_multiplier=None,
        )

        log_path = calib._trial_log_path
        record = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert "param_name" in record
        assert "direction" not in record
        assert "trial_multiplier" not in record

    def test_silently_ignores_write_errors(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        # Point to a non-existent directory
        calib._trial_log_path = Path("/nonexistent/dir/trial_log.jsonl")
        # Should not raise
        calib._append_trial_log(iteration=1, score=10.0, accepted=True, phase="single")


class TestRestoreCountersFromResult:
    """Test the _restore_counters_from_result method."""

    def test_restores_counters_from_valid_file(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        result_path = tmp_path / "result.json"
        result_path.write_text(
            json.dumps({
                "total_trial_count": 42,
                "total_iteration_count": 7,
            }),
            encoding="utf-8",
        )

        calib._restore_counters_from_result(result_path)

        assert calib._total_trial_count == 42
        assert calib._total_iteration_count == 7

    def test_uses_zero_for_missing_file(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        calib._restore_counters_from_result(tmp_path / "nonexistent.json")

        assert calib._total_trial_count == 0
        assert calib._total_iteration_count == 0

    def test_uses_zero_for_missing_fields(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        result_path = tmp_path / "result.json"
        result_path.write_text(json.dumps({}), encoding="utf-8")

        calib._restore_counters_from_result(result_path)

        assert calib._total_trial_count == 0
        assert calib._total_iteration_count == 0

    def test_skips_invalid_json_format(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        result_path = tmp_path / "result.json"
        result_path.write_text("[]", encoding="utf-8")  # JSON array, not dict

        calib._restore_counters_from_result(result_path)

        assert calib._total_trial_count == 0
        assert calib._total_iteration_count == 0


class TestBuildRunStats:
    """Test that _build_run_stats uses persistent counters."""

    def test_uses_total_trial_count_not_history_length(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        calib._total_trial_count = 100
        history = [{"iter": 1}]  # Only 1 entry in history

        stats = calib._build_run_stats(history)

        assert stats["calibration_count"] == 100
        assert stats["calibration_count"] != len(history)

    def test_handles_zero_trial_count_without_division_error(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        calib._total_trial_count = 0
        history = []

        stats = calib._build_run_stats(history)

        assert stats["calibration_count"] == 0
        assert stats["average_elapsed_sec"] == stats["total_elapsed_sec"]


class TestBuildResultPayload:
    """Test that _build_result_payload includes counters."""

    def test_includes_total_trial_count(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        calib._total_trial_count = 50
        calib._total_iteration_count = 10

        # Mock the dependencies needed for _build_result_payload
        with patch.object(calib, "_ensure_best_score_image", return_value=None):
            with patch.object(calib, "_ensure_best_overlay_image", return_value=None):
                with patch.object(calib, "_acceptance_payload", return_value={"passed": True, "mode": "test"}):
                    with patch.object(calib, "_build_calibration_summary", return_value={}):
                        payload = calib._build_result_payload(
                            best_score=5.0,
                            best_values={"pos_x": 0.0},
                            best_total_detail=MagicMock(
                                raw_total_score=5.0,
                                compared_board_count=1,
                                degrade_penalty=0.0,
                                has_critical_degrade=False,
                                degraded_boards=[],
                                isolated_outlier_boards=[],
                                board_scores=[],
                            ),
                            best_img=tmp_path / "test.png",
                            best_score_image=None,
                            best_overlay_image=None,
                            stop_reason="test",
                            history=[{"iter": 0}],
                            in_progress=False,
                        )

        assert payload["total_trial_count"] == 50
        assert payload["total_iteration_count"] == 10

    def test_prefers_historical_best_snapshot_when_present(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        historical_values = {"pos_x": 0.0}
        current_values = {"pos_x": 1.0}
        historical_detail = _make_total_detail(59.0, board_id="historical_board")
        current_detail = _make_total_detail(69.0, board_id="current_board")
        historical_img = tmp_path / "historical.png"
        current_img = tmp_path / "current.png"
        calib._historical_best_score = 59.0
        calib._historical_best_snapshot = {
            "score": 59.0,
            "values": historical_values.copy(),
            "total_detail": historical_detail,
            "img_path": historical_img,
        }

        summary_builder = MagicMock(return_value={})
        with patch.object(calib, "_acceptance_payload", return_value={"passed": True, "mode": "test"}):
            with patch.object(calib, "_build_calibration_summary", summary_builder):
                payload = calib._build_result_payload(
                    best_score=69.0,
                    best_values=current_values,
                    best_total_detail=current_detail,
                    best_img=current_img,
                    best_score_image=None,
                    best_overlay_image=None,
                    stop_reason="test",
                    history=[{"iter": 0}],
                    in_progress=False,
                )

        assert payload["best_score"] == 59.0
        assert payload["best_values"] == historical_values
        assert payload["best_image"] == str(historical_img)
        assert payload["best_metrics"]["board_scores"][0]["board_id"] == "historical_board"
        assert summary_builder.call_args.kwargs["best_score"] == 59.0
        assert summary_builder.call_args.kwargs["best_values"] == historical_values
        assert summary_builder.call_args.kwargs["best_total_detail"] is historical_detail
        assert summary_builder.call_args.kwargs["best_img"] == historical_img

    def test_write_progress_result_uses_historical_snapshot_for_final_artifacts(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        historical_values = {"pos_x": 0.0}
        current_values = {"pos_x": 1.0}
        historical_detail = _make_total_detail(59.0, board_id="historical_board")
        current_detail = _make_total_detail(69.0, board_id="current_board")
        historical_img = tmp_path / "historical.png"
        current_img = tmp_path / "current.png"
        calib._historical_best_score = 59.0
        calib._historical_best_snapshot = {
            "score": 59.0,
            "values": historical_values.copy(),
            "total_detail": historical_detail,
            "img_path": historical_img,
        }

        score_calls = []
        overlay_calls = []

        def _fake_score_image(img_path, total_detail, values=None):
            score_calls.append((img_path, total_detail, values.copy() if values else None))
            return img_path.with_name(f"{img_path.stem}_score.png")

        def _fake_overlay_image(img_path):
            overlay_calls.append(img_path)
            return img_path.with_name(f"{img_path.stem}_overlay.png")

        with patch.object(calib, "_ensure_best_score_image", side_effect=_fake_score_image):
            with patch.object(calib, "_ensure_best_overlay_image", side_effect=_fake_overlay_image):
                with patch.object(calib, "_acceptance_payload", return_value={"passed": True, "mode": "test"}):
                    with patch.object(calib, "_build_calibration_summary", return_value={}):
                        calib._write_progress_result(
                            best_score=69.0,
                            best_values=current_values,
                            best_total_detail=current_detail,
                            best_img=current_img,
                            stop_reason="finished",
                            history=[{"iter": 0}],
                            in_progress=False,
                        )

        result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert score_calls == [(historical_img, historical_detail, historical_values)]
        assert overlay_calls == [historical_img]
        assert result["best_score"] == 59.0
        assert result["best_values"] == historical_values
        assert result["best_image"] == str(historical_img)
        assert result["best_score_image"] == str(historical_img.with_name("historical_score.png"))
        assert result["best_overlay_image"] == str(historical_img.with_name("historical_overlay.png"))

    def test_write_progress_result_uses_historical_snapshot_for_in_progress_artifacts(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        historical_values = {"pos_x": 0.0}
        current_values = {"pos_x": 1.0}
        historical_detail = _make_total_detail(59.0, board_id="historical_board")
        current_detail = _make_total_detail(69.0, board_id="current_board")
        historical_img = tmp_path / "historical.png"
        current_img = tmp_path / "current.png"
        calib._historical_best_score = 59.0
        calib._historical_best_snapshot = {
            "score": 59.0,
            "values": historical_values.copy(),
            "total_detail": historical_detail,
            "img_path": historical_img,
        }

        score_calls = []
        overlay_calls = []

        def _fake_score_image(img_path, total_detail, values=None):
            score_calls.append((img_path, total_detail, values.copy() if values else None))
            return img_path.with_name(f"{img_path.stem}_score.png")

        def _fake_overlay_image(img_path):
            overlay_calls.append(img_path)
            return img_path.with_name(f"{img_path.stem}_overlay.png")

        with patch.object(calib, "_ensure_best_score_image", side_effect=_fake_score_image):
            with patch.object(calib, "_ensure_best_overlay_image", side_effect=_fake_overlay_image):
                with patch.object(calib, "_acceptance_payload", return_value={"passed": True, "mode": "test"}):
                    with patch.object(calib, "_build_calibration_summary", return_value={}):
                        calib._write_progress_result(
                            best_score=69.0,
                            best_values=current_values,
                            best_total_detail=current_detail,
                            best_img=current_img,
                            stop_reason="running",
                            history=[{"iter": 0}],
                            in_progress=True,
                        )

        result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert score_calls == [(historical_img, historical_detail, historical_values)]
        assert overlay_calls == [historical_img]
        assert result["in_progress"] is True
        assert result["best_score"] == 59.0
        assert result["best_values"] == historical_values
        assert result["best_image"] == str(historical_img)
        assert result["best_score_image"] == str(historical_img.with_name("historical_score.png"))
        assert result["best_overlay_image"] == str(historical_img.with_name("historical_overlay.png"))


class TestMovieFboCaptureScript:
    def test_capture_movie_uses_gl_widget_dimensions(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        captured = {}

        def _capture_script(_result_path, _target_topic, body_lines, **_kwargs):
            captured["body_lines"] = list(body_lines)
            raise RuntimeError("stop after capture")

        with patch("camera_calibration.render_dde_execute_script", side_effect=_capture_script):
            with pytest.raises(RuntimeError, match="stop after capture"):
                calib._capture_movie_via_dde_fbo("probe")

        body_lines = captured["body_lines"]
        assert "set wi [$wpath.gl0 cget -width]" in body_lines
        assert "set he [$wpath.gl0 cget -height]" in body_lines
        assert "set vno $View(ev.view)" in body_lines
        assert "scan $vno %d vno_int" in body_lines
        assert "dict get" not in " ".join(body_lines)

    def test_capture_movie_keeps_pre_fbo_section_free_of_event_pumping(self, tmp_path):
        cfg = _make_minimal_cfg(tmp_path)
        with patch.object(CameraCalibrator, "_materialize_custom_maker_templates"):
            with patch.object(CameraCalibrator, "_load_custom_templates", return_value={}):
                calib = CameraCalibrator(cfg)

        captured = {}

        def _capture_script(_result_path, _target_topic, body_lines, **_kwargs):
            captured["body_lines"] = list(body_lines)
            raise RuntimeError("stop after capture")

        with patch("camera_calibration.render_dde_execute_script", side_effect=_capture_script):
            with pytest.raises(RuntimeError, match="stop after capture"):
                calib._capture_movie_via_dde_fbo("probe")

        body_lines = captured["body_lines"]
        fbo_new_index = body_lines.index("set captureFBO [FBO new $wi $he -tex rgb -noclear]")
        pre_fbo_lines = body_lines[:fbo_new_index]
        assert "update" not in pre_fbo_lines
        assert "update idletasks" not in pre_fbo_lines
        assert "catch {UpdateView $View(ev.view)}" not in pre_fbo_lines
        assert 'catch {event generate .view${vno}.gl0 <Expose>}' not in pre_fbo_lines
        assert body_lines[fbo_new_index + 2] == "    FBO begin $captureFBO"
        assert body_lines[fbo_new_index + 3] == "    UpdateView $vno"
