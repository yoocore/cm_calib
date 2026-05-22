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

from camera_calibration import CameraCalibrator


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
                "min": -10.0,
                "max": 10.0,
                "step": 1.0,
                "min_step": 0.1,
                "decimals": 3,
                "unlocked": True,
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
