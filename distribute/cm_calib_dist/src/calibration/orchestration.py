"""OrchestrationMixin - history snapshots, trial logging, calibration summary, progress reporting, and runtime orchestration utilities."""
import copy
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, TextIO, Tuple

from src.calibration.calib_types import *
from src.calibration.utils import (
    _DEFAULT_BOUNDS_MULTIPLIER,
    _TeeStream,
    _board_prototype_family,
    _bootstrap_partial_template_dir,
    _build_explicit_parameter_config,
    _canonical_camera_group_name,
    _camera_name_from_output_dir,
    _clamp_to_parameter_bounds,
    _deep_merge_dict,
    _default_sim_output_root,
    _derive_camera_name_from_image_path,
    _format_scalar_value_map,
    _round_floats,
    _is_apriltag_board_type,
    _is_aruco_family_board_type,
    _is_aruco_grid_board_type,
    _is_circle_grid_board_type,
    _is_custom_marker_board_type,
    _path_to_json_string,
    _quantize_float,
    _resolve_parameter_bounds,
    _sim_output_root_legacy,
    _unlink_if_exists,
)
import atexit
import ctypes
import hashlib
import math
import msvcrt
import os
import random
import re
import shutil
import subprocess
import sys
import uuid
import warnings

import cv2
import numpy as np

from src.calibration.config import (
    _auto_upgrade_partial_checkerboards,
    _build_boards_from_annotation_rectangles,
    _cluster_1d,
    _default_bootstrap_template_path,
    _default_parameter_order,
    _extract_annotation_board_ids,
    _extract_annotation_rectangles,
    _group_annotation_rectangles,
    _load_bootstrap_template_specs,
    _materialize_auto_template_image,
    _masked_secondary_response_max,
    _normalize_annotation_board_id,
    _preprocess_auto_template_match_image,
    _rect_gap_distance,
    _resolved_bootstrap_config,
    _select_auto_template_crop,
    _sync_materialized_board_fields_from_calibrator,
    bootstrap_config_from_annotation,
)

from src.health.dde_health_check import (
    default_output_dir as _dde_default_output_dir,
    render_dde_execute_script,
    render_result_script,
    run_check_attempt,
)

# Note: CameraCalibrator is imported lazily inside functions that need it
# to avoid circular imports (camera_calibration imports OrchestrationMixin from here).

def _emit_cli_progress_json(payload: dict) -> None:
    """Emit progress JSON line for CLI consumers."""
    print("CALIBRATION_PROGRESS_JSON:", json.dumps(_round_floats(payload, skip_keys={"values", "best_values", "start_values", "final_values"}), ensure_ascii=False, sort_keys=True))

class OrchestrationMixin:

    def _remember_historical_best_snapshot(
        self,
        *,
        score: float,
        values: Dict[str, float],
        total_detail: TotalScoreDetail,
        img_path: Path,
    ) -> None:
        snapshot = getattr(self, "_historical_best_snapshot", None)
        previous_score: Optional[float] = None
        if isinstance(snapshot, dict):
            try:
                previous_score = float(snapshot.get("score"))
            except (TypeError, ValueError):
                previous_score = None
        if previous_score is not None and score >= previous_score:
            return
        self._historical_best_score = float(score)
        self._historical_best_snapshot = {
            "score": float(score),
            "values": values.copy(),
            "total_detail": copy.deepcopy(total_detail),
            "img_path": Path(img_path),
        }

    def _resolve_best_snapshot_state(
        self,
        *,
        best_score: float,
        best_values: Dict[str, float],
        best_total_detail: TotalScoreDetail,
        best_img: Path,
    ) -> Tuple[float, Dict[str, float], TotalScoreDetail, Path]:
        snapshot = getattr(self, "_historical_best_snapshot", None)
        if not isinstance(snapshot, dict):
            return best_score, best_values, best_total_detail, best_img
        snapshot_values = snapshot.get("values")
        snapshot_total_detail = snapshot.get("total_detail")
        snapshot_img = snapshot.get("img_path")
        if not isinstance(snapshot_values, dict) or snapshot_total_detail is None or snapshot_img is None:
            return best_score, best_values, best_total_detail, best_img
        try:
            snapshot_score = float(snapshot.get("score"))
        except (TypeError, ValueError):
            snapshot_score = best_score
        return snapshot_score, dict(snapshot_values), snapshot_total_detail, Path(snapshot_img)

    def _make_history_entry(
        self,
        iteration: int,
        total_detail: TotalScoreDetail,
        img_path: Path,
        accepted: bool,
        failed_reason: Optional[str] = None,
        meta: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        entry: Dict[str, object] = {
            "iter": iteration,
            "total_score": total_detail.total_score,
            "raw_total_score": total_detail.raw_total_score,
            "compared_board_count": total_detail.compared_board_count,
            "degrade_penalty": total_detail.degrade_penalty,
            "has_critical_degrade": total_detail.has_critical_degrade,
            "degraded_boards": total_detail.degraded_boards,
            "isolated_outlier_boards": total_detail.isolated_outlier_boards,
            "board_scores": [
                {
                    "board_id": s.board_id,
                    "board_type": s.board_type,
                    "compared": s.compared,
                    "reference_visible": s.reference_visible,
                    "sim_visible": s.sim_visible,
                    "score": s.total_score,
                    "rmse": s.rmse,
                    "mean_error": s.mean_error,
                    "max_error": s.max_error,
                    "miss_rate": s.miss_rate,
                    "matched_point_count": s.matched_point_count,
                    "failed_reason": s.failed_reason,
                }
                for s in total_detail.board_scores
            ],
            "accepted": accepted,
            "failed_reason": failed_reason or total_detail.failed_reason,
            "image": str(img_path),
            "values": self._snapshot_values(),
        }
        if meta:
            entry.update(meta)
        return entry

    def _append_trial_log(
        self,
        *,
        iteration: int,
        score: float,
        accepted: bool,
        phase: str,
        param_name: Optional[str] = None,
        direction: Optional[str] = None,
        trial_multiplier: Optional[float] = None,
        accepted_reason: Optional[str] = None,
        failed_reason: Optional[str] = None,
        recovered: bool = False,
        elapsed_sec: Optional[float] = None,
    ) -> None:
        try:
            self._trial_log_path.parent.mkdir(parents=True, exist_ok=True)
            record: Dict[str, object] = {
                "iteration": iteration,
                "score": score,
                "accepted": accepted,
                "phase": phase,
                "timestamp": datetime.now().astimezone().isoformat(),
            }
            if param_name is not None:
                record["param_name"] = param_name
            if direction is not None:
                record["direction"] = direction
            if trial_multiplier is not None:
                record["trial_multiplier"] = trial_multiplier
            if accepted_reason is not None:
                record["accepted_reason"] = accepted_reason
            if failed_reason is not None:
                record["failed_reason"] = failed_reason
            if recovered:
                record["recovered"] = recovered
            if elapsed_sec is not None:
                record["elapsed_sec"] = elapsed_sec
            with open(self._trial_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            print(f"Warning: failed to append trial log: {exc}")

    @staticmethod
    def _format_duration_stats(seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000.0)))
        hours, rem_ms = divmod(total_ms, 3600 * 1000)
        minutes, rem_ms = divmod(rem_ms, 60 * 1000)
        secs, millis = divmod(rem_ms, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

    def _build_run_stats(self, history: List[dict]) -> dict:
        calibration_count = self._total_trial_count
        total_elapsed_sec = max(0.0, time.perf_counter() - self.run_started_perf)
        average_elapsed_sec = total_elapsed_sec / max(1, calibration_count)
        return {
            "calibration_count": calibration_count,
            "total_elapsed_sec": total_elapsed_sec,
            "average_elapsed_sec": average_elapsed_sec,
            "total_elapsed_text": self._format_duration_stats(total_elapsed_sec),
            "average_elapsed_text": self._format_duration_stats(average_elapsed_sec),
        }

    def _camera_summary_name(self) -> str:
        return _camera_name_from_output_dir(self.output_dir)

    def _build_score_statistics(
        self,
        best_total_detail: TotalScoreDetail,
    ) -> Dict[str, object]:
        """Build quick score statistics for summary."""
        compared_scores = [
            s.total_score
            for s in best_total_detail.board_scores
            if s.compared
        ]

        if not compared_scores:
            return {
                "min_board_score": None,
                "max_board_score": None,
                "avg_board_score": None,
                "median_board_score": None,
                "best_board_id": None,
                "worst_board_id": None,
            }

        min_score = min(compared_scores)
        max_score = max(compared_scores)
        avg_score = sum(compared_scores) / len(compared_scores)
        median_score = float(np.median(np.array(compared_scores)))

        best_board = next(
            s for s in best_total_detail.board_scores
            if s.compared and s.total_score == max_score
        )
        worst_board = next(
            s for s in best_total_detail.board_scores
            if s.compared and s.total_score == min_score
        )

        return {
            "min_board_score": min_score,
            "max_board_score": max_score,
            "avg_board_score": avg_score,
            "median_board_score": median_score,
            "best_board_id": best_board.board_id,
            "worst_board_id": worst_board.board_id,
        }

    def _build_calibration_summary(
        self,
        *,
        best_score: float,
        best_values: Dict[str, float],
        best_total_detail: TotalScoreDetail,
        best_img: Path,
        best_score_image: Optional[Path],
        stop_reason: str,
        history: List[dict],
        run_stats: Dict[str, object],
        acceptance: Dict[str, object],
        in_progress: bool,
    ) -> Dict[str, object]:
        initial_entry = history[0] if history else {}
        latest_entry = history[-1] if history else {}
        initial_score = float(initial_entry.get("total_score", best_score))
        initial_values = dict(initial_entry.get("values", best_values))
        current_iter_index = int(latest_entry.get("iter", 0))
        current_iter_score = float(latest_entry.get("total_score", best_score))
        iteration_round_count = sum(
            1 for entry in history if entry.get("phase") == "iteration_start"
        )
        return {
            "camera": self._camera_summary_name(),
            "in_progress": in_progress,
            "start_score": initial_score,
            "final_score": best_score,
            "score_improvement": initial_score - best_score,
            "start_values": initial_values,
            "final_values": best_values,
            "current_iter_index": current_iter_index,
            "current_iter_score": current_iter_score,
            "iteration_round_count": iteration_round_count,
            "history_event_count": len(history),
            "total_elapsed_sec": run_stats["total_elapsed_sec"],
            "total_elapsed_text": run_stats["total_elapsed_text"],
            "average_elapsed_sec": run_stats["average_elapsed_sec"],
            "average_elapsed_text": run_stats["average_elapsed_text"],
            "stop_reason": stop_reason,
            "passed": acceptance["passed"],
            "acceptance_mode": acceptance["mode"],
            "compared_board_count": best_total_detail.compared_board_count,
            "best_image": str(best_img),
            "best_score_image": str(best_score_image) if best_score_image else None,
            "score_statistics": self._build_score_statistics(best_total_detail),
        }

    def _print_calibration_summary(self, summary: Dict[str, object]) -> None:
        print(
            "Calibration summary: "
            f"camera={summary['camera']} "
            f"start_score={float(summary['start_score']):.2f} "
            f"final_score={float(summary['final_score']):.2f} "
            f"improvement={float(summary['score_improvement']):.2f} "
            f"rounds={int(summary['iteration_round_count'])} "
            f"elapsed={summary['total_elapsed_text']} "
            f"stop_reason={summary['stop_reason']} "
            f"passed={summary['passed']}"
        )

        score_stats = summary.get('score_statistics', {})
        if score_stats and score_stats.get('max_board_score') is not None:
            print(
                "Score statistics: "
                f"min={float(score_stats['min_board_score']):.2f} "
                f"max={float(score_stats['max_board_score']):.2f} "
                f"avg={float(score_stats['avg_board_score']):.2f} "
                f"median={float(score_stats['median_board_score']):.2f} "
                f"best_board={score_stats['best_board_id']} "
                f"worst_board={score_stats['worst_board_id']}"
            )

        print(
            "Start values:",
            _format_scalar_value_map(dict(summary["start_values"])),
        )
        print(
            "Final values:",
            _format_scalar_value_map(dict(summary["final_values"])),
        )

    def _build_score_breakdown(
        self,
        best_total_detail: TotalScoreDetail,
        best_score: float,
    ) -> Dict[str, object]:
        """Build detailed score breakdown for user transparency."""
        board_map = {b.board_id: b for b in self.boards}
        weighted_scores: Dict[str, Dict[str, object]] = {}
        total_weight = 0.0
        weighted_sum = 0.0
        isolated_outlier_set = set(best_total_detail.isolated_outlier_boards)

        for score in best_total_detail.board_scores:
            if not score.compared:
                continue

            board = board_map.get(score.board_id)
            if board is None:
                continue

            weight = board.weight
            effective_weight = weight
            weighted_contribution = effective_weight * score.total_score

            weighted_scores[score.board_id] = {
                "raw_score": score.total_score,
                "weight": weight,
                "effective_weight": effective_weight,
                "weighted_contribution": weighted_contribution,
                "is_isolated_outlier": score.board_id in isolated_outlier_set,
            }

            total_weight += effective_weight
            if score.board_id not in isolated_outlier_set:
                weighted_sum += weighted_contribution

        weighted_average = weighted_sum / total_weight if total_weight > 0 else 0.0
        degrade_contribution = best_total_detail.degrade_penalty

        return {
            "weighted_scores": weighted_scores,
            "total_weight": total_weight,
            "weighted_average": weighted_average,
            "degrade_contribution": degrade_contribution,
            "final_total_score": best_score,
            "isolated_outlier_boards": best_total_detail.isolated_outlier_boards,
            "degraded_boards": best_total_detail.degraded_boards,
        }

    def _build_result_payload(
        self,
        *,
        best_score: float,
        best_values: Dict[str, float],
        best_total_detail: TotalScoreDetail,
        best_img: Path,
        best_score_image: Optional[Path],
        best_overlay_image: Optional[Path],
        stop_reason: str,
        history: List[dict],
        in_progress: bool,
    ) -> dict:
        best_score, best_values, best_total_detail, best_img = self._resolve_best_snapshot_state(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
        )
        updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        run_stats = self._build_run_stats(history)
        acceptance = self._acceptance_payload(best_total_detail)
        summary = self._build_calibration_summary(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            best_score_image=best_score_image,
            stop_reason=stop_reason,
            history=history,
            run_stats=run_stats,
            acceptance=acceptance,
            in_progress=in_progress,
        )
        return {
            "boards": [
                {
                    "board_id": b.board_id,
                    "board_type": b.board_type,
                    "weight": b.weight,
                    "critical": b.critical,
                }
                for b in self.boards
            ],
            "comparison_mode": self.comparison_mode,
            "score_scope": self.score_scope,
            "output_dir": str(self.output_dir),
            "best_score": best_score,
            "best_values": best_values,
            "acceptance": acceptance,
            "best_metrics": {
                "raw_total_score": best_total_detail.raw_total_score,
                "compared_board_count": best_total_detail.compared_board_count,
                "degrade_penalty": best_total_detail.degrade_penalty,
                "has_critical_degrade": best_total_detail.has_critical_degrade,
                "degraded_boards": best_total_detail.degraded_boards,
                "isolated_outlier_boards": best_total_detail.isolated_outlier_boards,
                "board_scores": [
                    {
                        "board_id": s.board_id,
                        "board_type": s.board_type,
                        "compared": s.compared,
                        "reference_visible": s.reference_visible,
                        "sim_visible": s.sim_visible,
                        "score": s.total_score,
                        "rmse": s.rmse,
                        "mean_error": s.mean_error,
                        "max_error": s.max_error,
                        "miss_rate": s.miss_rate,
                        "matched_point_count": s.matched_point_count,
                        "failed_reason": s.failed_reason,
                    }
                    for s in best_total_detail.board_scores
                ],
                "score_breakdown": self._build_score_breakdown(best_total_detail, best_score),
            },
            "best_image": str(best_img),
            "best_score_image": str(best_score_image) if best_score_image else None,
            "best_overlay_image": str(best_overlay_image) if best_overlay_image else None,
            "live_log": str(self.live_log_path) if self.live_log_path else None,
            "run_session_id": self.run_session_id,
            "started_at": self.run_started_at,
            "updated_at": updated_at,
            "finished_at": None if in_progress else updated_at,
            "stop_reason": stop_reason,
            "history_count": len(history),
            "total_trial_count": self._total_trial_count,
            "total_iteration_count": self._total_iteration_count,
            "run_stats": run_stats,
            "summary": summary,
            "strategy_adaptation": self._strategy_state_payload(),
            "in_progress": in_progress,
            "history": history,
        }

    def _write_progress_result(
        self,
        *,
        best_score: float,
        best_values: Dict[str, float],
        best_total_detail: TotalScoreDetail,
        best_img: Path,
        stop_reason: str,
        history: List[dict],
        in_progress: bool,
    ) -> None:
        best_score, best_values, best_total_detail, best_img = self._resolve_best_snapshot_state(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
        )
        best_score_image = self._ensure_best_score_image(
            best_img,
            best_total_detail,
            values=best_values,
        )
        best_overlay_image = self._ensure_best_overlay_image(best_img)
        result = self._build_result_payload(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            best_score_image=best_score_image,
            best_overlay_image=best_overlay_image,
            stop_reason=stop_reason,
            history=history,
            in_progress=in_progress,
        )
        result_path = self.output_dir / "result.json"
        payload = _round_floats(result, skip_keys={"values", "best_values", "start_values", "final_values"})
        payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload_bytes) > 10 * 1024 * 1024 and result_path.exists():
            archive_name = f"result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            result_path.rename(self.output_dir / archive_name)
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        if bool(getattr(self, "print_progress_json", False)):
            summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
            _emit_cli_progress_json(
                {
                    "camera": summary.get("camera"),
                    "output_dir": result.get("output_dir"),
                    "result_json": str(self.output_dir / "result.json"),
                    "in_progress": bool(result.get("in_progress", False)),
                    "live_log": str(self.live_log_path) if self.live_log_path else None,
                    "best_score": result.get("best_score"),
                    "best_image": result.get("best_image"),
                    "best_score_image": result.get("best_score_image"),
                    "best_overlay_image": result.get("best_overlay_image"),
                    "current_iter_index": summary.get("current_iter_index"),
                    "current_iter_score": summary.get("current_iter_score"),
                    "current_iter_image": getattr(self, "_last_eval_image", None) or str(best_img),
                    "final_score": summary.get("final_score"),
                    "stop_reason": result.get("stop_reason") or summary.get("stop_reason"),
                    "start_score": summary.get("start_score"),
                    "calib_phase": self._calib_phase,
                    "calib_dir_index": self._calib_dir_index,
                    "calib_total_dirs": self._calib_total_dirs,
                    "calib_max_iters": self._calib_max_iters,
                    "calib_round_index": self._calib_round_index,
                    "calib_round_count": self._calib_round_count,
                    "calib_overall_total_iters": self._calib_overall_total_iters,
                }
            )

    def _flush_progress_if_needed(
        self,
        *,
        best_score: float,
        best_values: Dict[str, float],
        best_total_detail: TotalScoreDetail,
        best_img: Path,
        stop_reason: str,
        history: List[dict],
    ) -> None:
        if len(history) % self.progress_flush_every != 0:
            return
        self._write_progress_result(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason=stop_reason,
            history=self._trim_history(history),
            in_progress=True,
        )

    def _fatal_initial_board_failures(self, total_detail: TotalScoreDetail) -> List[str]:
        board_map = {board.board_id: board for board in self.boards}
        failures: List[str] = []
        for score in total_detail.board_scores:
            if not score.compared:
                continue
            board = board_map.get(score.board_id)
            if board is None:
                continue
            if board.custom_detector == "template_match":
                fail_threshold = float(board.fail_penalty) * 0.999
            else:
                fail_threshold = max(1.0, float(board.fail_penalty) * 0.95)
            if score.success or float(score.total_score) < fail_threshold:
                continue
            failed_reason = score.failed_reason or "fail_penalty_reached"
            failures.append(
                f"{score.board_id}:{score.total_score:.3f}({failed_reason})"
            )
        return failures

    def _raise_if_initial_board_failures(self, total_detail: TotalScoreDetail) -> None:
        failures = self._fatal_initial_board_failures(total_detail)
        if not failures:
            return
        raise RuntimeError(
            "Initial evaluation aborted due to fatal board scores: " + ", ".join(failures)
        )

    def _trim_history(self, history: List[dict]) -> List[dict]:
        if self.max_history_entries <= 0:
            return history
        if len(history) <= self.max_history_entries:
            return history
        kept = [history[0]]
        tail = history[-(self.max_history_entries - 1):]
        kept.extend(tail)
        return kept

    def _prune_intermediate_images(self, final_best_img: Path) -> None:
        """Delete intermediate .png, keep only initial and final best."""
        keep_stems = {"initial", "initial_score", "initial_overlay"}
        best_stem = final_best_img.stem
        keep_stems.add(best_stem)
        keep_stems.add(f"{best_stem}_score")
        keep_stems.add(f"{best_stem}_overlay")
        for img_path in list(self.output_dir.glob("*.png")):
            if img_path.stem not in keep_stems:
                try:
                    img_path.unlink()
                except OSError:
                    pass

# ============================
# Runtime state and constants
# ============================

_LIVE_LOG_PRIMARY_STDOUT: Optional[TextIO] = None
_LIVE_LOG_PRIMARY_STDERR: Optional[TextIO] = None
_LIVE_LOG_STREAM: Optional[TextIO] = None
_LIVE_LOG_PATH: Optional[Path] = None
_LIVE_LOG_ATEXIT_REGISTERED = False
_DDE_RECOVERY_ERROR_MARKERS = (
    "remote server cannot handle this command",
    "timed out waiting for",
    "did not execute",
    "exec failed",
    "dde dispatch circuit recovery",
)
_RUNTIME_SESSION_LOCK_STREAM: Optional[TextIO] = None
_RUNTIME_SESSION_LOCK_PATH: Optional[Path] = None
_RUNTIME_SESSION_LOCK_ATEXIT_REGISTERED = False
_VEHICLE_WRITEBACK_CONTEXT_CACHE: Dict[str, Optional[dict]] = {}
_VEHICLE_WRITEBACK_METADATA_PREFIX = "# camera_calibration.writeback "

_VEHICLE_SENSOR_NAME_RE = re.compile(
    r"^(?P<prefix>\s*Sensor\.(?P<index>\d+)\.name\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
)
_VEHICLE_SENSOR_ACTIVE_RE = re.compile(
    r"^(?P<prefix>\s*Sensor\.(?P<index>\d+)\.Active\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
)
_VEHICLE_SENSOR_REF_PARAM_RE = re.compile(
    r"^(?P<prefix>\s*Sensor\.(?P<index>\d+)\.Ref\.Param\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
)
_VEHICLE_SENSOR_POS_RE = re.compile(
    r"^(?P<prefix>\s*Sensor\.(?P<index>\d+)\.pos\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
)
_VEHICLE_SENSOR_ROT_RE = re.compile(
    r"^(?P<prefix>\s*Sensor\.(?P<index>\d+)\.rot\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
)
_VEHICLE_SENSOR_PARAM_VALUE_RE = re.compile(
    r"^(?P<prefix>\s*Sensor\.Param\.(?P<index>\d+)\.(?P<field>[A-Za-z0-9_.]+)\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
)

# ============================
# Runtime orchestration functions
# ============================

def _cleanup_live_log() -> None:
    global _LIVE_LOG_PRIMARY_STDOUT, _LIVE_LOG_PRIMARY_STDERR, _LIVE_LOG_STREAM, _LIVE_LOG_PATH

    if _LIVE_LOG_PRIMARY_STDOUT is not None:
        sys.stdout = _LIVE_LOG_PRIMARY_STDOUT
    if _LIVE_LOG_PRIMARY_STDERR is not None:
        sys.stderr = _LIVE_LOG_PRIMARY_STDERR

    log_stream = _LIVE_LOG_STREAM
    _LIVE_LOG_PRIMARY_STDOUT = None
    _LIVE_LOG_PRIMARY_STDERR = None
    _LIVE_LOG_STREAM = None
    _LIVE_LOG_PATH = None
    if log_stream is None:
        return
    try:
        log_stream.flush()
    except Exception:
        pass
    try:
        log_stream.close()
    except Exception:
        pass

def _cleanup_runtime_session_lock() -> None:
    global _RUNTIME_SESSION_LOCK_STREAM, _RUNTIME_SESSION_LOCK_PATH

    lock_stream = _RUNTIME_SESSION_LOCK_STREAM
    _RUNTIME_SESSION_LOCK_STREAM = None
    _RUNTIME_SESSION_LOCK_PATH = None
    if lock_stream is None:
        return
    try:
        lock_stream.seek(0)
        msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    try:
        lock_stream.close()
    except Exception:
        pass

def _acquire_runtime_session_lock(output_dir: Path, config_path: Path) -> Path:
    global _RUNTIME_SESSION_LOCK_STREAM, _RUNTIME_SESSION_LOCK_PATH
    global _RUNTIME_SESSION_LOCK_ATEXIT_REGISTERED

    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / ".camera_calibration.runtime.lock"
    if _RUNTIME_SESSION_LOCK_STREAM is not None and _RUNTIME_SESSION_LOCK_PATH == lock_path:
        return lock_path

    if _RUNTIME_SESSION_LOCK_STREAM is not None:
        _cleanup_runtime_session_lock()

    lock_stream = open(lock_path, "a+b")
    existing_payload = ""
    try:
        lock_stream.seek(0)
        existing_payload = lock_stream.read().decode("utf-8", errors="replace").strip()
    except PermissionError:
        existing_payload = "metadata unavailable (lock holder denied shared read)"
    lock_stream.seek(0, 2)
    if lock_stream.tell() == 0:
        lock_stream.write(b"\n")
        lock_stream.flush()
    lock_stream.seek(0)

    try:
        msvcrt.locking(lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        lock_stream.close()
        detail = existing_payload or "metadata unavailable"
        raise RuntimeError(
            "Another calibration session is already running for "
            f"{output_dir.as_posix()}. Active lock metadata: {detail}"
        ) from exc

    payload = {
        "pid": os.getpid(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "config": str(config_path),
        "output_dir": str(output_dir),
        "argv": sys.argv,
    }
    lock_stream.seek(0)
    lock_stream.truncate()
    lock_stream.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    lock_stream.flush()

    if not _RUNTIME_SESSION_LOCK_ATEXIT_REGISTERED:
        atexit.register(_cleanup_runtime_session_lock)
        _RUNTIME_SESSION_LOCK_ATEXIT_REGISTERED = True
    _RUNTIME_SESSION_LOCK_STREAM = lock_stream
    _RUNTIME_SESSION_LOCK_PATH = lock_path
    return lock_path

def _configure_live_log_for_output_dir(output_dir: Path, resume_from_result: bool) -> Path:
    global _LIVE_LOG_PRIMARY_STDOUT, _LIVE_LOG_PRIMARY_STDERR, _LIVE_LOG_STREAM, _LIVE_LOG_PATH
    global _LIVE_LOG_ATEXIT_REGISTERED

    output_dir.mkdir(parents=True, exist_ok=True)
    log_name = "continue_resume.log" if resume_from_result else "run.log"
    log_path = output_dir / log_name
    if _LIVE_LOG_STREAM is not None and _LIVE_LOG_PATH == log_path:
        return log_path

    if _LIVE_LOG_STREAM is not None:
        _cleanup_live_log()

    log_stream = open(log_path, "w", encoding="utf-8", buffering=1)
    primary_stdout = sys.stdout
    primary_stderr = sys.stderr
    if not _LIVE_LOG_ATEXIT_REGISTERED:
        atexit.register(_cleanup_live_log)
        _LIVE_LOG_ATEXIT_REGISTERED = True
    _LIVE_LOG_PRIMARY_STDOUT = primary_stdout
    _LIVE_LOG_PRIMARY_STDERR = primary_stderr
    _LIVE_LOG_STREAM = log_stream
    _LIVE_LOG_PATH = log_path
    sys.stdout = _TeeStream(primary_stdout, log_stream)
    sys.stderr = _TeeStream(primary_stderr, log_stream)
    return log_path

def _configure_live_log(cfg: dict, resume_from_result: bool, project_root: Optional[Path] = None) -> Path:
    output_dir = _resolve_config_output_dir(cfg, project_root=project_root)
    cfg["output_dir"] = str(output_dir)
    return _configure_live_log_for_output_dir(output_dir, resume_from_result)

def _default_output_name_from_config(config_path: Optional[Path]) -> str:
    if config_path is not None:
        name = config_path.stem
        for prefix in ("camera.", "config."):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        name = name.replace(".", "_").strip("_")
        if name:
            return name
    return "camera_calibration_run"

def _resolve_config_output_dir(cfg: dict, config_path: Optional[Path] = None, project_root: Optional[Path] = None) -> Path:
    raw_output_dir = str(cfg.get("output_dir", "")).strip()
    if raw_output_dir:
        return Path(raw_output_dir)
    if project_root is not None:
        return _default_sim_output_root(project_root) / _default_output_name_from_config(config_path)
    return _default_sim_output_root() / _default_output_name_from_config(config_path)

def _build_isolated_output_dir(prefix: str, camera_parent: Optional[str] = None, project_root: Optional[Path] = None) -> Path:
    """Build an isolated output directory under SimOutput.

    If `camera_parent` is provided, the returned path will be
    `SimOutput / camera_parent / {prefix}_{ts}` so that runs for the
    same camera are grouped under the same parent directory.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sim_root = _default_sim_output_root(project_root) if project_root is not None else _default_sim_output_root()
    if camera_parent:
        return sim_root / camera_parent / f"{prefix}_{ts}"
    return sim_root / f"{prefix}_{ts}"

def _camera_name_from_config_path(config_path: Optional[Path]) -> str:
    return _default_output_name_from_config(config_path)

def _camera_history_summary_path(camera_name: str, project_root: Path) -> Path:
    return _default_sim_output_root(project_root) / _canonical_camera_group_name(camera_name) / "camera_summary.json"

def _camera_history_summary_compact_path(camera_name: str, project_root: Path) -> Path:
    return _default_sim_output_root(project_root) / _canonical_camera_group_name(camera_name) / "camera_summary_compact.json"

def _iter_camera_history_dirs(camera_name: str, project_root: Path) -> List[Path]:
    camera_group = _canonical_camera_group_name(camera_name)
    dirs: list[Path] = []
    for root in (_default_sim_output_root(project_root), _sim_output_root_legacy()):
        candidate = root / camera_group
        if candidate.exists() and candidate.is_dir():
            dirs.append(candidate)
    return dirs

def _load_json_if_exists(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _resolve_score_scope_from_cfg(cfg: Optional[dict]) -> Optional[str]:
    if not isinstance(cfg, dict):
        return None
    raw_scope = cfg.get("score_scope")
    if raw_scope is None:
        raw_scope = cfg.get("history_score_scope")
    if raw_scope is None:
        raw_scope = cfg.get("scoring_scope")
    if raw_scope is None:
        return None
    scope = str(raw_scope).strip()
    return scope or None

def _resolve_score_scope_from_payload(payload: Optional[dict]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    raw_scope = payload.get("score_scope")
    if raw_scope is None:
        raw_scope = payload.get("history_score_scope")
    if raw_scope is None:
        raw_scope = payload.get("scoring_scope")
    if raw_scope is None:
        return None
    scope = str(raw_scope).strip()
    return scope or None

def _build_run_digest_from_result_payload(
    payload: dict,
    result_path: Path,
    *,
    include_in_progress: bool = False,
) -> Optional[dict]:
    if payload.get("in_progress") and not include_in_progress:
        return None

    summary = payload.get("summary")
    if isinstance(summary, dict):
        digest = dict(summary)
    else:
        history = payload.get("history") or []
        initial_entry = history[0] if history else {}
        initial_score = float(initial_entry.get("total_score", payload.get("best_score", 0.0)))
        final_score = float(payload.get("best_score", initial_score))
        start_values = dict(initial_entry.get("values") or payload.get("best_values") or {})
        final_values = dict(payload.get("best_values") or {})
        iteration_round_count = sum(
            1 for entry in history if entry.get("phase") == "iteration_start"
        )
        run_stats = payload.get("run_stats") or {}
        acceptance = payload.get("acceptance") or {}
        digest = {
            "camera": Path(payload.get("output_dir", result_path.parent)).name,
            "in_progress": False,
            "start_score": initial_score,
            "final_score": final_score,
            "score_improvement": initial_score - final_score,
            "start_values": start_values,
            "final_values": final_values,
            "iteration_round_count": iteration_round_count,
            "history_event_count": payload.get("history_count", len(history)),
            "total_elapsed_sec": run_stats.get("total_elapsed_sec"),
            "total_elapsed_text": run_stats.get("total_elapsed_text"),
            "average_elapsed_sec": run_stats.get("average_elapsed_sec"),
            "average_elapsed_text": run_stats.get("average_elapsed_text"),
            "stop_reason": payload.get("stop_reason"),
            "passed": acceptance.get("passed"),
            "acceptance_mode": acceptance.get("mode"),
            "compared_board_count": (payload.get("best_metrics") or {}).get("compared_board_count"),
            "best_image": payload.get("best_image"),
            "best_score_image": payload.get("best_score_image"),
        }

    digest["result_json"] = str(result_path)
    digest["output_dir"] = payload.get("output_dir", str(result_path.parent))
    digest["score_scope"] = _resolve_score_scope_from_payload(payload)
    digest["started_at"] = payload.get("started_at")
    digest["finished_at"] = payload.get("finished_at")
    digest["camera"] = _camera_name_from_output_dir(Path(digest["output_dir"]))
    return digest

def _build_campaign_digest(campaign_payload: dict, campaign_summary_path: Path) -> dict:
    best_run = campaign_payload.get("best_run") or {}
    refine = campaign_payload.get("refine") or {}
    explore = campaign_payload.get("explore") or {}
    return {
        "campaign_summary_json": str(campaign_summary_path),
        "campaign_output_dir": campaign_payload.get("campaign_output_dir"),
        "best_stage": best_run.get("stage"),
        "best_score": best_run.get("best_score"),
        "best_result_json": best_run.get("result_json"),
        "explore_start_count": explore.get("start_count"),
        "explore_max_iters": explore.get("max_iters"),
        "refine_max_iters": refine.get("max_iters"),
    }

def _format_duration_stats_seconds(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000.0)))
    hours, rem_ms = divmod(total_ms, 3600 * 1000)
    minutes, rem_ms = divmod(rem_ms, 60 * 1000)
    secs, millis = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

def _build_camera_history_overview(summary: dict) -> Optional[dict]:
    runs = [item for item in (summary.get("runs") or []) if isinstance(item, dict)]
    if not runs:
        return None

    first_run = runs[0]
    latest_run = runs[-1]
    best_run = summary.get("best_run") if isinstance(summary.get("best_run"), dict) else None

    total_iteration_round_count = sum(int(item.get("iteration_round_count") or 0) for item in runs)
    total_history_event_count = sum(int(item.get("history_event_count") or 0) for item in runs)
    total_iter_count = sum(
        max(
            0,
            int(item.get("history_event_count") or 0) - int(item.get("iteration_round_count") or 0),
        )
        for item in runs
    )
    total_elapsed_sec = sum(float(item.get("total_elapsed_sec") or 0.0) for item in runs)
    total_score_improvement = sum(float(item.get("score_improvement") or 0.0) for item in runs)
    run_count = len(runs)
    average_run_elapsed_sec = total_elapsed_sec / max(1, run_count)
    average_iter_count = total_iter_count / max(1, run_count)
    average_iter_elapsed_sec = total_elapsed_sec / max(1, total_iter_count)

    first_start_score = float(first_run.get("start_score") or 0.0)
    latest_final_score = float(latest_run.get("final_score") or 0.0)
    best_final_score = float(best_run.get("final_score") or latest_final_score) if best_run else latest_final_score

    return {
        "started_at": first_run.get("started_at"),
        "finished_at": latest_run.get("finished_at"),
        "run_count": run_count,
        "campaign_count": int(summary.get("campaign_count") or 0),
        "passed_run_count": int(summary.get("passed_run_count") or 0),
        "total_iter_count": total_iter_count,
        "average_iter_count": average_iter_count,
        "total_round_count": total_iteration_round_count,
        "total_elapsed_sec": total_elapsed_sec,
        "total_elapsed_text": _format_duration_stats_seconds(total_elapsed_sec),
        "average_run_elapsed_sec": average_run_elapsed_sec,
        "average_run_elapsed_text": _format_duration_stats_seconds(average_run_elapsed_sec),
        "average_iter_elapsed_sec": average_iter_elapsed_sec,
        "average_iter_elapsed_text": _format_duration_stats_seconds(average_iter_elapsed_sec),
        "first_start_score": first_start_score,
        "latest_final_score": latest_final_score,
        "best_final_score": best_final_score,
        "net_score_improvement_to_latest": first_start_score - latest_final_score,
        "net_score_improvement_to_best": first_start_score - best_final_score,
    }

def _build_camera_history_summary(camera_name: str, project_root: Path) -> dict:
    history_dirs = _iter_camera_history_dirs(camera_name, project_root)
    run_digests: List[dict] = []
    campaign_digests: List[dict] = []

    for history_dir in history_dirs:
        for result_path in sorted(history_dir.rglob("result.json")):
            payload = _load_json_if_exists(result_path)
            if not isinstance(payload, dict):
                continue
            digest = _build_run_digest_from_result_payload(payload, result_path)
            if digest is not None:
                run_digests.append(digest)

        for campaign_summary_path in sorted(history_dir.rglob("campaign_summary.json")):
            payload = _load_json_if_exists(campaign_summary_path)
            if isinstance(payload, dict):
                campaign_digests.append(
                    _build_campaign_digest(payload, campaign_summary_path)
                )

    run_digests.sort(
        key=lambda item: (
            str(item.get("started_at") or ""),
            str(item.get("finished_at") or ""),
            str(item.get("result_json") or ""),
        )
    )

    best_run = None
    if run_digests:
        best_run = min(run_digests, key=lambda item: float(item.get("final_score", float("inf"))))
    latest_run = run_digests[-1] if run_digests else None
    best_improvement_run = None
    if run_digests:
        best_improvement_run = max(
            run_digests,
            key=lambda item: float(item.get("score_improvement", float("-inf"))),
        )

    return {
        "camera": camera_name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_count": len(run_digests),
        "passed_run_count": sum(1 for item in run_digests if bool(item.get("passed"))),
        "campaign_count": len(campaign_digests),
        "best_run": best_run,
        "latest_run": latest_run,
        "best_improvement_run": best_improvement_run,
        "campaigns": campaign_digests,
        "runs": run_digests,
    }

def _write_camera_history_summary(camera_name: str, project_root: Path) -> Tuple[Path, dict]:
    summary = _build_camera_history_summary(camera_name, project_root)
    summary_path = _camera_history_summary_path(camera_name, project_root)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary_path, summary

def _build_camera_history_summary_compact(summary: dict) -> dict:
    return {
        "camera": summary.get("camera"),
        "generated_at": summary.get("generated_at"),
        "overview": _build_camera_history_overview(summary),
        "first_run": (summary.get("runs") or [None])[0],
        "best_run": summary.get("best_run"),
        "latest_run": summary.get("latest_run"),
        "best_improvement_run": summary.get("best_improvement_run"),
        "latest_campaign": (summary.get("campaigns") or [None])[-1],
    }


def _build_camera_trend_data(camera_name: str, project_root: Path) -> list[dict]:
    """Return time-series trend data for charting: per-run score, board sig, timestamp."""
    summary = _build_camera_history_summary(camera_name, project_root)
    runs = [r for r in (summary.get("runs") or []) if isinstance(r, dict)]
    trend = []
    for r in runs:
        trend.append({
            "timestamp": r.get("timestamp", ""),
            "final_score": r.get("final_score"),
            "start_score": r.get("start_score"),
            "boards_count": r.get("boards_count"),
            "board_signature": r.get("board_signature", ""),
            "mode": r.get("mode", ""),
        })
    return trend


def _write_camera_history_summary_compact(camera_name: str, summary: dict, project_root: Path) -> Path:
    compact_summary = _build_camera_history_summary_compact(summary)
    compact_summary_path = _camera_history_summary_compact_path(camera_name, project_root)
    compact_summary_path.parent.mkdir(parents=True, exist_ok=True)
    compact_summary_path.write_text(
        json.dumps(compact_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return compact_summary_path

def _print_camera_history_summary(summary: dict, summary_path: Path) -> None:
    print(
        "Camera history summary: "
        f"camera={summary['camera']} "
        f"runs={summary['run_count']} "
        f"passed_runs={summary['passed_run_count']} "
        f"campaigns={summary['campaign_count']}"
    )

    best_run = summary.get("best_run")
    if isinstance(best_run, dict):
        print(
            "Camera best run: "
            f"start_score={float(best_run.get('start_score', 0.0)):.6f} "
            f"final_score={float(best_run.get('final_score', 0.0)):.6f} "
            f"rounds={int(best_run.get('iteration_round_count', 0))} "
            f"elapsed={best_run.get('total_elapsed_text')} "
            f"stop_reason={best_run.get('stop_reason')}"
        )
        print("Camera best start values:", _format_scalar_value_map(dict(best_run.get("start_values") or {})))
        print("Camera best final values:", _format_scalar_value_map(dict(best_run.get("final_values") or {})))

    latest_run = summary.get("latest_run")
    if isinstance(latest_run, dict):
        print(
            "Camera latest run: "
            f"start_score={float(latest_run.get('start_score', 0.0)):.6f} "
            f"final_score={float(latest_run.get('final_score', 0.0)):.6f} "
            f"rounds={int(latest_run.get('iteration_round_count', 0))} "
            f"elapsed={latest_run.get('total_elapsed_text')} "
            f"stop_reason={latest_run.get('stop_reason')}"
        )

    print("Camera summary JSON:", str(summary_path))

def _print_camera_history_summary_compact(compact_summary_path: Path) -> None:
    print("Camera compact summary JSON:", str(compact_summary_path))

def _marker_name_for_output_dir(output_dir: Path) -> str:
    return f"{output_dir.name}_last.json"

def _camera_scope_output_dir(output_dir: Path, project_root: Optional[Path] = None) -> Path:
    """Return the camera-scoped root directory under SimOutput for an output path."""
    sim_root = _default_sim_output_root(project_root) if project_root is not None else _default_sim_output_root()
    for root in (sim_root, _sim_output_root_legacy()):
        try:
            relative = output_dir.relative_to(root)
            if relative.parts:
                return root / _canonical_camera_group_name(relative.parts[0])
        except Exception:
            continue
    return output_dir

def _marker_path_for_output_dir(output_dir: Path, project_root: Optional[Path] = None) -> Path:
    """Return marker path for an output_dir.

    Prefer placing the marker inside the camera-scoped directory under
    SimOutput, otherwise fall back to the output_dir itself.
    """
    camera_scope_dir = _camera_scope_output_dir(output_dir, project_root)
    return camera_scope_dir / _marker_name_for_output_dir(output_dir)

def _write_run_marker(marker_path: Path, payload: dict) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def _find_latest_result_path(fallback_output_dir: Path) -> Path:
    direct_result_path = fallback_output_dir / "result.json"
    if direct_result_path.exists():
        return direct_result_path

    prefix = f"{fallback_output_dir.name}_"
    latest_result: Optional[Path] = None
    latest_mtime = float("-inf")
    try:
        for child in fallback_output_dir.parent.iterdir():
            if not child.is_dir() or not child.name.startswith(prefix):
                continue
            candidate = child / "result.json"
            if not candidate.exists():
                continue
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                continue
            if mtime > latest_mtime:
                latest_result = candidate
                latest_mtime = mtime
    except OSError:
        return direct_result_path

    return latest_result or direct_result_path

def _read_latest_result_path(marker_path: Path, fallback_output_dir: Path) -> Path:
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        return _find_latest_result_path(fallback_output_dir)
    result_json = marker.get("result_json")
    if isinstance(result_json, str) and result_json.strip():
        return Path(result_json)

    output_dir = marker.get("output_dir")
    if isinstance(output_dir, str) and output_dir.strip():
        marker_output_result = Path(output_dir) / "result.json"
        if marker_output_result.exists():
            return marker_output_result

    return _find_latest_result_path(fallback_output_dir)

def _compute_board_signature(boards: Any) -> Optional[frozenset]:
    if not isinstance(boards, list) or not boards:
        return None
    entries = []
    for board in boards:
        if not isinstance(board, dict):
            return None
        bid = board.get("board_id")
        btype = board.get("board_type")
        if not bid or not btype:
            return None
        entries.append((str(bid), str(btype)))
    return frozenset(entries)
# ── Historical params pool (cross-signature) ──────────────────────────

def _compute_board_signature_hash(signature: frozenset) -> str:
    """Deterministic short hash of a board signature frozenset."""
    sorted_entries = sorted(signature, key=lambda x: (x[0], x[1]))
    raw = ",".join(f"{bid}:{btype}" for bid, btype in sorted_entries)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def _params_pool_path(camera_name: str, project_root: Path) -> Path:
    """Path to historical params pool JSON for a camera."""
    root = _default_sim_output_root(project_root)
    return root / _canonical_camera_group_name(camera_name) / "historical_params_pool.json"

def _load_params_pool(camera_name: str, project_root: Path) -> dict:
    """Load params pool from disk, creating empty if missing.

    On first call (pool file absent), migrates from old result.jsons.
    """
    path = _params_pool_path(camera_name, project_root)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version") == 2:
                return data
        except Exception:
            pass

    # Pool doesn't exist or is corrupt - migrate from old result.jsons
    pool = {"version": 2, "camera_name": camera_name, "entries": {}}
    history_dirs = _iter_camera_history_dirs(camera_name, project_root)
    for history_dir in history_dirs:
        for result_path in sorted(history_dir.rglob("result.json")):
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                digest = _build_run_digest_from_result_payload(payload, result_path)
                if digest is None:
                    continue
                boards = payload.get("boards")
                if not isinstance(boards, list):
                    continue
                final_score = digest.get("final_score")
                final_values = digest.get("final_values")
                if final_score is None or not final_values:
                    continue
                try:
                    final_score = float(final_score)
                except (TypeError, ValueError):
                    continue
                params = {name: float(value) for name, value in final_values.items()
                          if isinstance(value, (int, float))}
                if not params:
                    continue
                _update_params_pool_with_result(pool, boards, final_score, params)
            except Exception:
                continue
    if pool["entries"]:
        _save_params_pool(camera_name, pool, project_root=project_root)
        print(f"[pool_migrate] Built pool from old runs: {len(pool['entries'])} signature(s)")
    return pool

def _save_params_pool(camera_name: str, pool: dict, project_root: Optional[Path] = None) -> None:
    """Save params pool to disk."""
    pool["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    path = _params_pool_path(camera_name, project_root=project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_round_floats(pool, skip_keys={"best_params"}), indent=2, ensure_ascii=False), encoding="utf-8")

def _pool_entry_for_current_config(pool: dict, cfg: dict) -> Optional[dict]:
    """Look up pool entry matching the board config from cfg."""
    boards = cfg.get("boards")
    sig = _compute_board_signature(boards)
    if sig is None:
        return None
    sig_hash = _compute_board_signature_hash(sig)
    return pool.get("entries", {}).get(sig_hash)

def _find_best_pool_entry_across_signatures(pool: dict) -> Optional[dict]:
    """Find pool entry with the lowest (best) score across all signatures."""
    best: Optional[dict] = None
    for entry in pool.get("entries", {}).values():
        score = entry.get("best_score")
        if score is None:
            continue
        if best is None or float(score) < float(best.get("best_score", float("inf"))):
            best = entry
    return best

def _update_params_pool_with_result(pool: dict, boards: list, score: float, params: dict) -> dict:
    """Update pool with a new calibration result for the given boards.

    Returns updated pool (mutated in-place for convenience).
    Only stores entries with a better score than what is already known.
    """
    sig = _compute_board_signature(boards)
    if sig is None:
        return pool
    sig_hash = _compute_board_signature_hash(sig)
    entries = pool.setdefault("entries", {})
    existing = entries.get(sig_hash)
    if existing and float(score) >= float(existing.get("best_score", float("inf"))) - 1e-9:
        return pool
    entries[sig_hash] = {
        "board_signature": [list(pair) for pair in sorted(sig, key=lambda x: (x[0], x[1]))],
        "best_score": float(score),
        "best_params": dict(params),
        "boards_count": len(boards) if isinstance(boards, list) else 0,
        "run_timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return pool

def _evaluate_params_on_current_config(
    config_path: Path,
    cfg: dict,
    camera_name: str,
    params: Dict[str, float],
) -> Optional[float]:
    """Apply params as initial values and capture+score on current boards.

    Returns the score, or None on failure.
    """
    from src.calibration.camera_calibration import CameraCalibrator  # noqa: E402
    try:
        temp_cfg = _cfg_with_initial_values(copy.deepcopy(cfg), params)
        calib = CameraCalibrator(temp_cfg, config_path=config_path)
        calib._apply_initial_value_map_with_retry(params, f"eval_{camera_name}")
        total_detail, _ = calib.evaluate("eval", baseline_metrics=None)
        return float(total_detail.total_score)
    except Exception as exc:
        print(f"[eval_on_current] Failed to evaluate {camera_name}: {exc}")
        return None

def _vehicle_writeback_backup_path(vehicle_path: Path) -> Path:
    return vehicle_path.parent / f"{vehicle_path.name}.calib.bk"

def _normalize_vehicle_sensor_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())

def _tokenize_camera_like_name(value: str) -> List[str]:
    return [token for token in re.split(r"[^a-z0-9]+", str(value).casefold()) if token]

def _camera_name_matches_vehicle_sensor(camera_name: str, sensor_name: str) -> bool:
    camera_tokens = _tokenize_camera_like_name(camera_name)
    sensor_tokens = _tokenize_camera_like_name(sensor_name)
    if not camera_tokens or not sensor_tokens:
        return False
    if _normalize_vehicle_sensor_name(camera_name) == _normalize_vehicle_sensor_name(sensor_name):
        return True
    token_pos = 0
    for token in sensor_tokens:
        if token_pos < len(camera_tokens) and token == camera_tokens[token_pos]:
            token_pos += 1
    return token_pos == len(camera_tokens)

def _read_sensor_values_from_vehicle(
    vehicle_path: Path,
    camera_name: str,
    sensor_name_override: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    if not vehicle_path.exists():
        print(f"Vehicle file not found: {vehicle_path}")
        return None

    text = vehicle_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    sensor_name_by_index: Dict[str, str] = {}
    ref_param_by_sensor_index: Dict[str, str] = {}
    pos_by_sensor_index: Dict[str, str] = {}
    rot_by_sensor_index: Dict[str, str] = {}

    for line in lines:
        stripped = line.rstrip("\r\n")
        match = _VEHICLE_SENSOR_NAME_RE.match(stripped)
        if match is not None:
            sensor_name_by_index[match.group("index")] = match.group("value").strip()
            continue
        match = _VEHICLE_SENSOR_REF_PARAM_RE.match(stripped)
        if match is not None:
            ref_value = match.group("value").strip()
            if ref_value:
                ref_param_by_sensor_index[match.group("index")] = ref_value
            continue
        match = _VEHICLE_SENSOR_POS_RE.match(stripped)
        if match is not None:
            pos_by_sensor_index[match.group("index")] = match.group("value").strip()
            continue
        match = _VEHICLE_SENSOR_ROT_RE.match(stripped)
        if match is not None:
            rot_by_sensor_index[match.group("index")] = match.group("value").strip()
            continue

    target_name = sensor_name_override or camera_name
    sensor_index: Optional[str] = None
    for idx, name in sensor_name_by_index.items():
        if _camera_name_matches_vehicle_sensor(target_name, name):
            sensor_index = idx
            break

    if sensor_index is None:
        print(f"Sensor '{target_name}' not found in vehicle file {vehicle_path}")
        return None

    ref_param_index = ref_param_by_sensor_index.get(sensor_index)
    if not ref_param_index:
        print(f"Sensor.{sensor_index} has no Ref.Param in {vehicle_path}")
        return None

    pos_text = pos_by_sensor_index.get(sensor_index, "")
    pos_parts = pos_text.split()
    if len(pos_parts) < 3:
        print(f"Sensor.{sensor_index}.pos has fewer than 3 values: {pos_text!r}")
        return None

    rot_text = rot_by_sensor_index.get(sensor_index, "")
    rot_parts = rot_text.split()
    if len(rot_parts) < 3:
        print(f"Sensor.{sensor_index}.rot has fewer than 3 values: {rot_text!r}")
        return None

    param_values: Dict[str, str] = {}
    for line in lines:
        stripped = line.rstrip("\r\n")
        match = _VEHICLE_SENSOR_PARAM_VALUE_RE.match(stripped)
        if match is not None and match.group("index") == ref_param_index:
            param_values[match.group("field")] = match.group("value").strip()

    def _parse_float(text: str) -> Optional[float]:
        try:
            return float(text)
        except (ValueError, TypeError):
            return None

    result: Dict[str, float] = {}
    result["pos_x"] = _parse_float(pos_parts[0])
    result["pos_y"] = _parse_float(pos_parts[1])
    result["pos_z"] = _parse_float(pos_parts[2])
    result["roll"] = _parse_float(rot_parts[0])
    result["pitch"] = _parse_float(rot_parts[1])
    result["yaw"] = _parse_float(rot_parts[2])

    fov_text = param_values.get("FoV", "")
    fov_value = _parse_float(fov_text)
    if fov_value is not None:
        result["lens_fov"] = fov_value

    scale_text = param_values.get("ImageScaling", "")
    scale_value = _parse_float(scale_text)
    if scale_value is not None:
        result["lens_scale"] = scale_value

    ppo_text = param_values.get("PrincipalPntOffset", "")
    ppo_parts = ppo_text.split()
    if len(ppo_parts) >= 2:
        ppo_x = _parse_float(ppo_parts[0])
        ppo_y = _parse_float(ppo_parts[1])
        if ppo_x is not None:
            result["lens_offset_x"] = ppo_x
        if ppo_y is not None:
            result["lens_offset_y"] = ppo_y

    result = {k: v for k, v in result.items() if v is not None}

    if not result:
        print(f"No valid values extracted for sensor '{target_name}' from {vehicle_path}")
        return None

    print(f"Read {len(result)} values from vehicle file for sensor '{target_name}': "
          + ", ".join(f"{k}={v}" for k, v in result.items()))
    return result

def _read_vehicle_initial_values_via_dde(camera_name: str, project_root: Optional[Path] = None) -> Optional[Dict[str, float]]:
    runtime_context = _probe_runtime_vehicle_context(project_root)
    if runtime_context is None:
        return None
    vehicle_path = runtime_context.get("vehicle_path")
    if not vehicle_path:
        return None
    sensor_name = runtime_context.get("sensor_name")
    print(f"Reading vehicle file: {vehicle_path}")
    print(f"Sensor name override: {sensor_name}")
    return _read_sensor_values_from_vehicle(Path(vehicle_path), camera_name, sensor_name)

def _read_vehicle_initial_values_mandatory(
    camera_name: str,
    project_root: Optional[Path] = None,
    max_retries: int = 3,
    retry_delay_sec: float = 2.0,
) -> Dict[str, float]:
    for attempt in range(1, max_retries + 1):
        values = _read_vehicle_initial_values_via_dde(camera_name, project_root=project_root)
        if values:
            return values
        if attempt < max_retries:
            print(f"Vehicle DDE read attempt {attempt}/{max_retries} failed, retrying in {retry_delay_sec}s...")
            time.sleep(retry_delay_sec)
    raise RuntimeError(
        f"Cannot read initial values from vehicle file for '{camera_name}' "
        f"after {max_retries} attempts. Ensure CarMaker is running with the correct vehicle loaded."
    )

def _parse_runtime_vehicle_probe_detail(detail: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for raw_line in str(detail).splitlines():
        line = str(raw_line).strip()
        if not line:
            continue
        parts = line.split(None, 1)
        key = parts[0].strip().lower()
        value = parts[1].strip() if len(parts) > 1 else ""
        parsed[key] = value
    return parsed

def _probe_runtime_vehicle_context(project_root: Optional[Path] = None) -> Optional[dict]:
    output_dir = _dde_default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = f"camera_calibration_vehicle_writeback_{uuid.uuid4().hex}"
    script_text = render_result_script(
        output_dir / f"{probe_name}.txt",
        [
            "set lines {}",
            'if {[info exists TestRun(FName)]} {lappend lines [list testrun $TestRun(FName)]} else {lappend lines [list testrun ""]}',
            'if {[catch {IFileRead TestRun "Vehicle"} vehicle_msg]} {error $vehicle_msg}',
            'lappend lines [list vehicle $vehicle_msg]',
            'join $lines "\\n"',
        ],
    )
    result = run_check_attempt(
        probe_name,
        "TclEval",
        "CarMaker",
        output_dir,
        script_text,
        5.0,
    )
    if not result.get("ok"):
        print(
            "Skipped vehicle writeback runtime probe: "
            f"kind={result.get('kind')} detail={result.get('detail')}"
        )
        return None

    parsed = _parse_runtime_vehicle_probe_detail(str(result.get("detail") or ""))
    vehicle_key = parsed.get("vehicle", "").strip()
    if not vehicle_key:
        print("Skipped vehicle writeback runtime probe: vehicle path was empty")
        return None

    resolved = Path(project_root).resolve() if project_root is not None else Path(__file__).resolve().parents[2]
    vehicle_path = resolved / "Data" / "Vehicle" / Path(vehicle_key.replace("\\", "/"))
    return {
        "project_root": resolved,
        "testrun": parsed.get("testrun", "").strip() or None,
        "vehicle_key": vehicle_key,
        "vehicle_path": vehicle_path,
    }

def _resolve_vehicle_writeback_context(config_path: Path, cfg: dict) -> Optional[dict]:
    cache_key = str(config_path.resolve())
    if cache_key in _VEHICLE_WRITEBACK_CONTEXT_CACHE:
        cached = _VEHICLE_WRITEBACK_CONTEXT_CACHE[cache_key]
        return copy.deepcopy(cached) if isinstance(cached, dict) else None

    payload = cfg.get("vehicle_writeback") if isinstance(cfg.get("vehicle_writeback"), dict) else {}
    print(f"[writeback] Resolving context: config={config_path}, vehicle_writeback keys={list(payload.keys())}")
    if payload.get("enabled") is False:
        print(f"[writeback] SKIPPED: vehicle_writeback.enabled is False")
        return None

    project_root = Path(payload.get("project_root", None) or Path(__file__).resolve().parents[2])
    vehicle_key = str(payload.get("vehicle", payload.get("vehicle_key", ""))).strip()
    vehicle_path: Optional[Path] = None
    testrun_name = str(payload.get("testrun", "")).strip() or None
    sensor_name = str(payload.get("sensor_name", "")).strip() or None

    if vehicle_key:
        candidate = Path(vehicle_key.replace("\\", "/"))
        if candidate.is_absolute():
            vehicle_path = candidate
            print(f"[writeback] Using absolute vehicle_key: {vehicle_key}")
        else:
            parts = list(candidate.parts)
            if len(parts) >= 2 and parts[0].lower() == "data" and parts[1].lower() == "vehicle":
                candidate = Path(*parts[2:])
            vehicle_path = project_root / "Data" / "Vehicle" / candidate
            print(f"[writeback] Resolved vehicle_path from vehicle_key: {vehicle_path}")
    else:
        runtime_context = _probe_runtime_vehicle_context(project_root)
        if runtime_context is not None:
            project_root = Path(runtime_context.get("project_root") or project_root)
            vehicle_key = str(runtime_context.get("vehicle_key") or "").strip()
            vehicle_path = Path(runtime_context.get("vehicle_path")) if runtime_context.get("vehicle_path") else None
            testrun_name = testrun_name or runtime_context.get("testrun")

    if vehicle_path is None:
        print(f"[writeback] SKIPPED: vehicle_path is None, probe result was: {runtime_context}")
        print(f"Skipped vehicle writeback: unable to resolve vehicle path for {config_path}")
        return None

    context = {
        "project_root": project_root,
        "testrun": testrun_name,
        "vehicle_key": vehicle_key or str(vehicle_path),
        "vehicle_path": vehicle_path,
        "sensor_name": sensor_name,
    }
    _VEHICLE_WRITEBACK_CONTEXT_CACHE[cache_key] = copy.deepcopy(context)
    return context

def _format_vehicle_float(value: float) -> str:
    text = f"{float(value):.12f}".rstrip("0").rstrip(".")
    return text or "0"

def _replace_vehicle_assignment_line(line: str, regex: re.Pattern[str], replacement_value: str) -> str:
    stripped = line.rstrip("\r\n")
    newline = line[len(stripped) :]
    match = regex.match(stripped)
    if match is None:
        return line
    return f"{match.group('prefix')}{replacement_value}{match.group('suffix')}{newline}"

def _select_vehicle_sensor_for_writeback(
    sensor_name_by_index: Dict[str, str],
    active_indexes: List[str],
    camera_name: str,
    explicit_sensor_name: Optional[str],
) -> Tuple[str, str]:
    if explicit_sensor_name:
        explicit_norm = _normalize_vehicle_sensor_name(explicit_sensor_name)
        for sensor_index, sensor_name in sensor_name_by_index.items():
            if _normalize_vehicle_sensor_name(sensor_name) == explicit_norm:
                return sensor_index, sensor_name
        raise RuntimeError(
            f"vehicle_writeback sensor_name {explicit_sensor_name!r} was not found. "
            f"Available sensors: {', '.join(sensor_name_by_index[index] for index in sorted(sensor_name_by_index, key=int))}"
        )

    if len(active_indexes) == 1:
        sensor_index = active_indexes[0]
        return sensor_index, sensor_name_by_index[sensor_index]

    matched_indexes = [
        sensor_index
        for sensor_index, sensor_name in sensor_name_by_index.items()
        if _camera_name_matches_vehicle_sensor(camera_name, sensor_name)
    ]
    if len(matched_indexes) == 1:
        sensor_index = matched_indexes[0]
        return sensor_index, sensor_name_by_index[sensor_index]

    raise RuntimeError(
        "Unable to resolve target sensor for vehicle writeback: "
        f"camera={camera_name}, active_indexes={active_indexes}, "
        f"available={', '.join(sensor_name_by_index[index] for index in sorted(sensor_name_by_index, key=int))}"
    )

def _write_best_values_to_vehicle_config(
    config_path: Path,
    cfg: dict,
    camera_name: str,
    best_score: float,
    values: Dict[str, float],
    project_root: Optional[Path] = None,
) -> Optional[dict]:
    print(f"[writeback] Called: config={config_path}, camera={camera_name}, best_score={float(best_score):.4f}, values_count={len(values)}")
    for k, v in sorted(values.items()):
        print(f"[writeback]   {k}={v}")
    context = _resolve_vehicle_writeback_context(config_path, cfg)
    if context is None:
        print(f"[writeback] SKIPPED: _resolve_vehicle_writeback_context returned None for config={config_path}")
        return None

    vehicle_path = Path(context["vehicle_path"])
    if not vehicle_path.exists():
        print(f"Skipped vehicle writeback: vehicle file not found at {vehicle_path}")
        return None
    # Pool-based write protection: re-evaluate all pool entries on current boards.
    # Historical scores came from different board configurations, so the only
    # fair comparison is to test each pool entry's params on CURRENT boards.
    best_re_eval_score: Optional[float] = None
    best_re_eval_params: Dict[str, float] = {}
    pool = _load_params_pool(camera_name, project_root=project_root)
    pool_entries = pool.get("entries", {})
    if pool_entries:
        print(f"[write_protect] Re-evaluating {len(pool_entries)} pool entries on current boards...")
        for sig_hash, entry in pool_entries.items():
            entry_params = entry.get("best_params", {})
            if not entry_params:
                continue
            original_score = entry.get("best_score")
            entry_score = _evaluate_params_on_current_config(
                config_path, cfg, camera_name, entry_params,
            )
            if entry_score is not None:
                print(f"  entry {sig_hash[:8]}: original={original_score}, re-evaluated={entry_score:.2f}")
                # Correct stored score to reflect current configuration (self-healing)
                if original_score is not None and abs(float(entry_score) - float(original_score)) > 1e-6:
                    entry["best_score"] = float(entry_score)
                    print(f"    -> corrected pool entry score from {original_score} to {entry_score:.2f}")
                if best_re_eval_score is None or entry_score < best_re_eval_score:
                    best_re_eval_score = entry_score
                    best_re_eval_params = dict(entry_params)
            else:
                print(f"  entry {sig_hash[:8]}: re-eval FAILED, original={original_score}")
        # Persist corrected pool scores (self-healing)
        _save_params_pool(camera_name, pool, project_root=project_root)
        if best_re_eval_score is not None:
            if float(best_score) > best_re_eval_score + 1e-6:
                print(
                    f"Pool better: writing pool best (score={best_re_eval_score:.2f}) ",
                    f"instead of current ({float(best_score):.2f}) ",
                    f"(camera={camera_name}, vehicle={vehicle_path})",
                )
                best_score = best_re_eval_score
                values = dict(best_re_eval_params)
            else:
                print(
                    f"[write_protect] Write OK: current {float(best_score):.2f} ",
                    f"<= re-eval best {best_re_eval_score:.2f}",
                )

    boards = cfg.get("boards")
    text = vehicle_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    sensor_name_by_index: Dict[str, str] = {}
    active_indexes: List[str] = []
    ref_param_by_sensor_index: Dict[str, str] = {}
    pos_line_by_sensor_index: Dict[str, int] = {}
    rot_line_by_sensor_index: Dict[str, int] = {}
    param_line_by_key: Dict[Tuple[str, str], int] = {}

    for line_index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        match = _VEHICLE_SENSOR_NAME_RE.match(stripped)
        if match is not None:
            sensor_name_by_index[match.group("index")] = match.group("value").strip()
            continue
        match = _VEHICLE_SENSOR_ACTIVE_RE.match(stripped)
        if match is not None:
            if match.group("value").strip() == "1":
                active_indexes.append(match.group("index"))
            continue
        match = _VEHICLE_SENSOR_REF_PARAM_RE.match(stripped)
        if match is not None:
            ref_value = match.group("value").strip()
            if ref_value:
                ref_param_by_sensor_index[match.group("index")] = ref_value
            continue
        match = _VEHICLE_SENSOR_POS_RE.match(stripped)
        if match is not None:
            pos_line_by_sensor_index[match.group("index")] = line_index
            continue
        match = _VEHICLE_SENSOR_ROT_RE.match(stripped)
        if match is not None:
            rot_line_by_sensor_index[match.group("index")] = line_index
            continue
        match = _VEHICLE_SENSOR_PARAM_VALUE_RE.match(stripped)
        if match is not None:
            param_line_by_key[(match.group("index"), match.group("field"))] = line_index

    sensor_index, sensor_name = _select_vehicle_sensor_for_writeback(
        sensor_name_by_index,
        active_indexes,
        camera_name,
        context.get("sensor_name"),
    )
    ref_param_index = ref_param_by_sensor_index.get(sensor_index)
    if not ref_param_index:
        raise RuntimeError(
            f"Vehicle sensor {sensor_name!r} is missing Sensor.{sensor_index}.Ref.Param in {vehicle_path}"
        )

    backup_path = _vehicle_writeback_backup_path(vehicle_path)
    backup_created = False
    if not backup_path.exists():
        shutil.copy2(vehicle_path, backup_path)
        backup_created = True

    changed_fields: List[str] = []

    pos_line_index = pos_line_by_sensor_index.get(sensor_index)
    if pos_line_index is not None:
        pos_value = " ".join(
            _format_vehicle_float(float(values[name]))
            for name in ("pos_x", "pos_y", "pos_z")
        )
        new_line = _replace_vehicle_assignment_line(lines[pos_line_index], _VEHICLE_SENSOR_POS_RE, pos_value)
        if new_line != lines[pos_line_index]:
            lines[pos_line_index] = new_line
            changed_fields.append(f"Sensor.{sensor_index}.pos")

    rot_line_index = rot_line_by_sensor_index.get(sensor_index)
    if rot_line_index is not None:
        rot_value = " ".join(
            _format_vehicle_float(float(values[name]))
            for name in ("roll", "pitch", "yaw")
        )
        new_line = _replace_vehicle_assignment_line(lines[rot_line_index], _VEHICLE_SENSOR_ROT_RE, rot_value)
        if new_line != lines[rot_line_index]:
            lines[rot_line_index] = new_line
            changed_fields.append(f"Sensor.{sensor_index}.rot")

    for field_name, param_name in (("FoV", "lens_fov"), ("ImageScaling", "lens_scale")):
        line_index = param_line_by_key.get((ref_param_index, field_name))
        if line_index is None:
            continue
        field_regex = re.compile(
            rf"^(?P<prefix>\s*Sensor\.Param\.{re.escape(ref_param_index)}\.{re.escape(field_name)}\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
        )
        new_line = _replace_vehicle_assignment_line(
            lines[line_index],
            field_regex,
            _format_vehicle_float(float(values[param_name])),
        )
        if new_line != lines[line_index]:
            lines[line_index] = new_line
            changed_fields.append(f"Sensor.Param.{ref_param_index}.{field_name}")

    principal_point_index = param_line_by_key.get((ref_param_index, "PrincipalPntOffset"))
    if principal_point_index is not None:
        principal_point_regex = re.compile(
            rf"^(?P<prefix>\s*Sensor\.Param\.{re.escape(ref_param_index)}\.PrincipalPntOffset\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$"
        )
        principal_point_value = " ".join(
            _format_vehicle_float(float(values[name]))
            for name in ("lens_offset_x", "lens_offset_y")
        )
        new_line = _replace_vehicle_assignment_line(
            lines[principal_point_index],
            principal_point_regex,
            principal_point_value,
        )
        if new_line != lines[principal_point_index]:
            lines[principal_point_index] = new_line
            changed_fields.append(f"Sensor.Param.{ref_param_index}.PrincipalPntOffset")

    metadata_line = (
        f"{_VEHICLE_WRITEBACK_METADATA_PREFIX}camera={camera_name} "
        f"sensor={sensor_name} best_score={float(best_score):.6f} "
        f"updated_at={datetime.now().astimezone().isoformat(timespec='seconds')}"
    )
    metadata_index = next(
        (index for index, line in enumerate(lines) if line.startswith(_VEHICLE_WRITEBACK_METADATA_PREFIX)),
        None,
    )
    if metadata_index is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] = lines[-1] + "\n"
        lines.append(metadata_line + "\n")
        changed_fields.append("vehicle_writeback_metadata")
    else:
        existing_line = lines[metadata_index].rstrip("\r\n")
        if existing_line != metadata_line:
            newline = lines[metadata_index][len(existing_line) :]
            lines[metadata_index] = metadata_line + (newline or "\n")
            changed_fields.append("vehicle_writeback_metadata")

    if changed_fields:
        vehicle_path.write_text("".join(lines), encoding="utf-8")

        verify_text = vehicle_path.read_text(encoding="utf-8")
        verify_mismatches = []
        for field in changed_fields:
            if field == f"Sensor.{sensor_index}.pos":
                expected = " ".join(_format_vehicle_float(float(values[name])) for name in ("pos_x", "pos_y", "pos_z"))
                for vline in verify_text.splitlines():
                    m = _VEHICLE_SENSOR_POS_RE.match(vline.rstrip())
                    if m and m.group("index") == sensor_index:
                        if m.group("value").strip() != expected:
                            verify_mismatches.append(f"pos: expected={expected}, actual={m.group('value').strip()}")
                        break
            elif field == f"Sensor.{sensor_index}.rot":
                expected = " ".join(_format_vehicle_float(float(values[name])) for name in ("roll", "pitch", "yaw"))
                for vline in verify_text.splitlines():
                    m = _VEHICLE_SENSOR_ROT_RE.match(vline.rstrip())
                    if m and m.group("index") == sensor_index:
                        if m.group("value").strip() != expected:
                            verify_mismatches.append(f"rot: expected={expected}, actual={m.group('value').strip()}")
                        break
            elif field.startswith(f"Sensor.Param.{ref_param_index}.FoV"):
                expected = _format_vehicle_float(float(values.get("lens_fov", 0)))
                for vline in verify_text.splitlines():
                    m = _VEHICLE_SENSOR_PARAM_VALUE_RE.match(vline.rstrip())
                    if m and m.group("index") == ref_param_index and m.group("field") == "FoV":
                        if m.group("value").strip() != expected:
                            verify_mismatches.append(f"FoV: expected={expected}, actual={m.group('value').strip()}")
                        break
            elif field.startswith(f"Sensor.Param.{ref_param_index}.ImageScaling"):
                expected = _format_vehicle_float(float(values.get("lens_scale", 0)))
                for vline in verify_text.splitlines():
                    m = _VEHICLE_SENSOR_PARAM_VALUE_RE.match(vline.rstrip())
                    if m and m.group("index") == ref_param_index and m.group("field") == "ImageScaling":
                        if m.group("value").strip() != expected:
                            verify_mismatches.append(f"ImageScaling: expected={expected}, actual={m.group('value').strip()}")
                        break
            elif field.startswith(f"Sensor.Param.{ref_param_index}.PrincipalPntOffset"):
                expected = " ".join(_format_vehicle_float(float(values[name])) for name in ("lens_offset_x", "lens_offset_y"))
                for vline in verify_text.splitlines():
                    m = _VEHICLE_SENSOR_PARAM_VALUE_RE.match(vline.rstrip())
                    if m and m.group("index") == ref_param_index and m.group("field") == "PrincipalPntOffset":
                        if m.group("value").strip() != expected:
                            verify_mismatches.append(f"PrincipalPntOffset: expected={expected}, actual={m.group('value').strip()}")
                        break
        if verify_mismatches:
            print(
                f"VEHICLE READBACK MISMATCH: path={vehicle_path}, sensor={sensor_name}, "
                f"mismatches={verify_mismatches}"
            )
        else:
            print(
                "Vehicle writeback: "
                f"path={vehicle_path}, sensor={sensor_name}, ref_param={ref_param_index}, "
                f"best_score={float(best_score):.6f}, "
                f"backup={'created' if backup_created else 'reused'}:{backup_path}, "
                f"changes={', '.join(changed_fields)}, readback=OK"
            )
    else:
        print(
            "Vehicle writeback: "
            f"path={vehicle_path}, sensor={sensor_name}, ref_param={ref_param_index}, "
            f"best_score={float(best_score):.6f}, "
            f"backup={'created' if backup_created else 'reused'}:{backup_path}, "
            f"changes=-"
        )
    # Update params pool after successful write
    if boards:
        _update_params_pool_with_result(pool, boards, float(best_score), values)
        _save_params_pool(camera_name, pool, project_root=project_root)

    return {
        "vehicle_path": str(vehicle_path),
        "vehicle_backup_path": str(backup_path),
        "sensor_name": sensor_name,
        "sensor_index": int(sensor_index),
        "ref_param_index": int(ref_param_index),
        "best_score": float(best_score),
        "changed_fields": changed_fields,
        "backup_created": backup_created,
        "testrun": context.get("testrun"),
        "vehicle_key": context.get("vehicle_key"),
    }

def _resolve_round_strategy_autotune_policy(cfg: dict) -> dict:
    default_unlock_parameter_step_multipliers = {
        "C": {
            "lens_scale": 6.0,
            "lens_offset_x": 6.0,
            "lens_offset_y": 6.0,
        },
        "S": {
            "lens_scale": 4.0,
            "lens_offset_x": 4.0,
            "lens_offset_y": 4.0,
        },
    }

    payload = cfg.get("round_strategy_autotune")
    if payload is False:
        return {
            "enabled": False,
            "activation_stagnation_rounds": 2,
            "plateau_score_delta": 0.75,
            "top_k_boards": 4,
            "min_board_score": 8.0,
            "min_target_board_count": 2,
            "min_family_board_count": 2,
            "min_family_share": 0.75,
            "priority_max_total_worsen_step": 0.5,
            "priority_max_total_worsen_cap": 4.5,
            "priority_min_total_improvement_floor": 0.6,
            "priority_tradeoff_ratio_floor": 0.6,
            "joint_max_single_worsen_cap": 4.5,
            "force_focus_activation_rounds": 1,
            "restrict_to_priority_boards": True,
            "dominant_family_restrict_to_priority_boards": False,
            "auto_switch_priority_boards": True,
            "priority_board_switch_streak_rounds": 2,
            "priority_board_switch_min_overlap_ratio": 0.6,
            "focus_rank_multiplier_step": 0.35,
            "focus_rank_multiplier_cap": 1.6,
            "focus_priority_multiplier_step": 0.08,
            "focus_priority_multiplier_cap": 1.35,
            "unlock_parameters_enabled": False,
            "unlock_parameter_activation_rounds": 2,
            "unlock_parameter_step_multipliers": default_unlock_parameter_step_multipliers,
            "deanchor_baseline_enabled": False,
            "deanchor_activation_stagnation_rounds": 2,
            "deanchor_repeated_signature_count": 2,
            "deanchor_score_delta": 0.05,
            "skip_refine_on_plateau": False,
            "skip_refine_activation_stagnation_rounds": 2,
            "skip_refine_score_delta": 0.05,
        }

    if not isinstance(payload, dict):
        payload = {}

    unlock_parameter_step_multipliers = copy.deepcopy(default_unlock_parameter_step_multipliers)
    raw_unlock_parameter_step_multipliers = payload.get("unlock_parameter_step_multipliers")
    if isinstance(raw_unlock_parameter_step_multipliers, dict):
        for raw_family, raw_mapping in raw_unlock_parameter_step_multipliers.items():
            family = str(raw_family).strip().upper()
            if not family or not isinstance(raw_mapping, dict):
                continue
            family_mapping = {}
            for raw_name, raw_value in raw_mapping.items():
                name = str(raw_name).strip()
                if not name:
                    continue
                try:
                    family_mapping[name] = max(0.0, float(raw_value))
                except Exception:
                    continue
            unlock_parameter_step_multipliers[family] = family_mapping

    return {
        "enabled": bool(payload.get("enabled", True)),
        "activation_stagnation_rounds": max(
            1, int(payload.get("activation_stagnation_rounds", 2))
        ),
        "plateau_score_delta": max(0.0, float(payload.get("plateau_score_delta", 0.75))),
        "top_k_boards": max(2, int(payload.get("top_k_boards", 4))),
        "min_board_score": max(0.0, float(payload.get("min_board_score", 8.0))),
        "min_target_board_count": max(
            1,
            int(
                payload.get(
                    "min_target_board_count",
                    payload.get("min_family_board_count", 2),
                )
            ),
        ),
        "min_family_board_count": max(2, int(payload.get("min_family_board_count", 2))),
        "min_family_share": min(1.0, max(0.0, float(payload.get("min_family_share", 0.75)))),
        "priority_max_total_worsen_step": max(
            0.0, float(payload.get("priority_max_total_worsen_step", 0.5))
        ),
        "priority_max_total_worsen_cap": max(
            0.0, float(payload.get("priority_max_total_worsen_cap", 4.5))
        ),
        "priority_min_total_improvement_floor": max(
            0.0, float(payload.get("priority_min_total_improvement_floor", 0.6))
        ),
        "priority_tradeoff_ratio_floor": max(
            0.0, float(payload.get("priority_tradeoff_ratio_floor", 0.6))
        ),
        "joint_max_single_worsen_cap": max(
            0.0, float(payload.get("joint_max_single_worsen_cap", 4.5))
        ),
        "force_focus_activation_rounds": max(
            1, int(payload.get("force_focus_activation_rounds", 1))
        ),
        "restrict_to_priority_boards": bool(payload.get("restrict_to_priority_boards", True)),
        "dominant_family_restrict_to_priority_boards": bool(
            payload.get("dominant_family_restrict_to_priority_boards", False)
        ),
        "auto_switch_priority_boards": bool(payload.get("auto_switch_priority_boards", True)),
        "priority_board_switch_streak_rounds": max(
            1,
            int(
                payload.get(
                    "priority_board_switch_streak_rounds",
                    payload.get("priority_family_switch_streak_rounds", 2),
                )
            ),
        ),
        "priority_board_switch_min_overlap_ratio": min(
            1.0,
            max(
                0.0,
                float(payload.get("priority_board_switch_min_overlap_ratio", 0.6)),
            ),
        ),
        "focus_rank_multiplier_step": max(
            0.0, float(payload.get("focus_rank_multiplier_step", 0.35))
        ),
        "focus_rank_multiplier_cap": max(
            1.0, float(payload.get("focus_rank_multiplier_cap", 1.6))
        ),
        "focus_priority_multiplier_step": max(
            0.0, float(payload.get("focus_priority_multiplier_step", 0.08))
        ),
        "focus_priority_multiplier_cap": max(
            1.0, float(payload.get("focus_priority_multiplier_cap", 1.35))
        ),
        "unlock_parameters_enabled": bool(payload.get("unlock_parameters_enabled", False)),
        "unlock_parameter_activation_rounds": max(
            1, int(payload.get("unlock_parameter_activation_rounds", 2))
        ),
        "unlock_parameter_step_multipliers": unlock_parameter_step_multipliers,
        "deanchor_baseline_enabled": bool(payload.get("deanchor_baseline_enabled", False)),
        "deanchor_activation_stagnation_rounds": max(
            1, int(payload.get("deanchor_activation_stagnation_rounds", 2))
        ),
        "deanchor_repeated_signature_count": max(
            1, int(payload.get("deanchor_repeated_signature_count", 2))
        ),
        "deanchor_score_delta": max(0.0, float(payload.get("deanchor_score_delta", 0.05))),
        "skip_refine_on_plateau": bool(payload.get("skip_refine_on_plateau", False)),
        "skip_refine_activation_stagnation_rounds": max(
            1, int(payload.get("skip_refine_activation_stagnation_rounds", 2))
        ),
        "skip_refine_score_delta": max(
            0.0, float(payload.get("skip_refine_score_delta", 0.05))
        ),
    }

def _round_entry_best_score(round_entry: dict) -> Optional[float]:
    try:
        if "best_score" in round_entry:
            return float(round_entry.get("best_score"))
        best_run = round_entry.get("best_run")
        if isinstance(best_run, dict) and "best_score" in best_run:
            return float(best_run.get("best_score"))
    except Exception:
        return None
    return None

def _round_entry_result_json(round_entry: dict) -> Optional[str]:
    if not isinstance(round_entry, dict):
        return None
    raw_result_json = round_entry.get("result_json")
    if isinstance(raw_result_json, str) and raw_result_json.strip():
        return raw_result_json
    best_run = round_entry.get("best_run")
    if isinstance(best_run, dict):
        raw_result_json = best_run.get("result_json")
        if isinstance(raw_result_json, str) and raw_result_json.strip():
            return raw_result_json
    return None

def _round_entry_best_values(round_entry: dict) -> Dict[str, float]:
    if not isinstance(round_entry, dict):
        return {}

    raw_values = round_entry.get("best_values")
    if not isinstance(raw_values, dict):
        best_run = round_entry.get("best_run")
        if isinstance(best_run, dict):
            raw_values = best_run.get("best_values")
    if not isinstance(raw_values, dict):
        return {}

    values: Dict[str, float] = {}
    for raw_name, raw_value in raw_values.items():
        name = str(raw_name).strip()
        if not name:
            continue
        try:
            values[name] = float(raw_value)
        except Exception:
            continue
    return values

def _round_value_signature(values: Dict[str, float]) -> Tuple[Tuple[str, float], ...]:
    return tuple((name, round(float(value), 6)) for name, value in sorted(values.items()))

def _count_matching_round_signatures(
    round_summaries: List[dict],
    target_round: dict,
    score_delta: float,
) -> int:
    target_score = _round_entry_best_score(target_round)
    target_values = _round_entry_best_values(target_round)
    if target_score is None or not target_values:
        return 0

    target_signature = _round_value_signature(target_values)
    tolerance = max(0.0, float(score_delta))
    match_count = 0
    for round_entry in round_summaries:
        round_score = _round_entry_best_score(round_entry)
        if round_score is None:
            continue
        round_values = _round_entry_best_values(round_entry)
        if not round_values:
            continue
        if _round_value_signature(round_values) != target_signature:
            continue
        if abs(float(round_score) - float(target_score)) > tolerance:
            continue
        match_count += 1
    return match_count

def _round_entry_current_param_order(round_entry: dict) -> List[str]:
    if not isinstance(round_entry, dict):
        return []
    raw_order = round_entry.get("current_param_order")
    if not isinstance(raw_order, list):
        return []
    return [str(name).strip() for name in raw_order if str(name).strip()]

def _current_round_plateau_stagnation_count(
    round_summaries: List[dict],
    score_delta: float,
) -> int:
    best_score: Optional[float] = None
    stagnation_count = 0
    tolerance = max(0.0, float(score_delta))
    for round_entry in round_summaries:
        score = _round_entry_best_score(round_entry)
        if score is None:
            continue
        if best_score is None or score < best_score - tolerance:
            best_score = score
            stagnation_count = 0
            continue
        stagnation_count += 1
    return stagnation_count

def _dominant_bottleneck_target_from_result_payload(
    result_payload: dict,
    cfg: dict,
    policy: dict,
) -> Optional[dict]:
    acceptance = result_payload.get("acceptance") or {}
    if bool(acceptance.get("passed", False)):
        return None

    acceptance_reason = str(acceptance.get("reason", "")).strip().lower()
    if "bottleneck" not in acceptance_reason:
        try:
            max_board_score = float(acceptance.get("max_board_score", 0.0))
            max_board_threshold = float(
                acceptance.get("bottleneck_board_score_max_threshold", 0.0)
            )
        except Exception:
            max_board_score = 0.0
            max_board_threshold = 0.0
        if max_board_threshold <= 0.0 or max_board_score <= max_board_threshold:
            return None

    best_metrics = result_payload.get("best_metrics") or {}
    raw_board_scores = best_metrics.get("board_scores") or []
    if not isinstance(raw_board_scores, list):
        return None

    priority_accept_cfg = cfg.get("priority_board_acceptance") or {}
    priority_board_ids = {
        str(board_id).strip()
        for board_id in priority_accept_cfg.get("board_ids", [])
        if str(board_id).strip()
    }
    isolated_outlier_boards = {
        str(board_id).strip()
        for board_id in (best_metrics.get("isolated_outlier_boards") or [])
        if str(board_id).strip()
    }

    candidates: List[dict] = []
    family_candidates: Dict[str, List[dict]] = {}
    for raw_board_score in raw_board_scores:
        if not isinstance(raw_board_score, dict):
            continue
        if not bool(raw_board_score.get("compared", False)):
            continue
        board_id = str(raw_board_score.get("board_id", "")).strip()
        if not board_id or board_id in isolated_outlier_boards:
            continue
        if (
            bool(policy.get("dominant_family_restrict_to_priority_boards", False))
            and priority_board_ids
            and board_id not in priority_board_ids
        ):
            continue
        family = _board_focus_family(board_id)
        if not family:
            continue
        try:
            score_value = float(raw_board_score.get("total_score", raw_board_score.get("score", 0.0)))
        except Exception:
            continue
        if score_value < float(policy.get("min_board_score", 8.0)):
            continue
        family_candidates.setdefault(family, []).append(
            {
                "board_id": board_id,
                "family": family,
                "score": score_value,
            }
        )
        candidates.append(
            {
                "board_id": board_id,
                "family": family,
                "score": score_value,
            }
        )

    if not candidates:
        return None

    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    selected = candidates[: max(1, int(policy.get("top_k_boards", 4)))]
    if len(selected) < int(policy.get("min_target_board_count", 2)):
        return None

    family_groups: Dict[str, List[dict]] = {}
    for item in selected:
        family_groups.setdefault(str(item["family"]), []).append(item)
    dominant_family = ""
    dominant_items: List[dict] = []
    family_share = 0.0
    if family_groups:
        dominant_family, dominant_items = max(
            family_groups.items(),
            key=lambda entry: (
                len(entry[1]),
                sum(float(item["score"]) for item in entry[1]),
            ),
        )
        family_share = len(dominant_items) / max(1, len(selected))
        if (
            len(dominant_items) >= int(policy.get("min_family_board_count", 2))
            and family_share >= float(policy.get("min_family_share", 0.75))
        ):
            dominant_scores = [float(item["score"]) for item in dominant_items]
            family_board_ids = [
                str(item["board_id"])
                for item in sorted(
                    family_candidates.get(str(dominant_family), []),
                    key=lambda item: float(item["score"]),
                    reverse=True,
                )
            ]
            return {
                "mode": "family",
                "family": dominant_family,
                "families": [dominant_family],
                "board_ids": [str(item["board_id"]) for item in dominant_items],
                "priority_board_ids": family_board_ids,
                "focus_board_ids": [str(item["board_id"]) for item in dominant_items],
                "board_count": len(dominant_items),
                "family_share": family_share,
                "max_score": max(dominant_scores),
                "avg_score": sum(dominant_scores) / len(dominant_scores),
            }

    selected_scores = [float(item["score"]) for item in selected]
    selected_families = sorted({str(item["family"]) for item in selected if str(item["family"])})
    return {
        "mode": "board_set",
        "family": dominant_family if len(selected_families) == 1 else "",
        "families": selected_families,
        "board_ids": [str(item["board_id"]) for item in selected],
        "priority_board_ids": [str(item["board_id"]) for item in selected],
        "focus_board_ids": [str(item["board_id"]) for item in selected],
        "board_count": len(selected),
        "family_share": family_share,
        "max_score": max(selected_scores),
        "avg_score": sum(selected_scores) / len(selected_scores),
    }

def _board_id_overlap_ratio(left_board_ids: List[str], right_board_ids: List[str]) -> float:
    left = {str(board_id).strip() for board_id in left_board_ids if str(board_id).strip()}
    right = {str(board_id).strip() for board_id in right_board_ids if str(board_id).strip()}
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))

def _priority_board_target_switch_streak(
    round_summaries: List[dict],
    cfg: dict,
    policy: dict,
) -> dict:
    streak = 0
    mode = ""
    family = ""
    board_ids: List[str] = []
    minimum_overlap_ratio = float(policy.get("priority_board_switch_min_overlap_ratio", 0.6))
    for round_entry in reversed(round_summaries):
        result_json = _round_entry_result_json(round_entry)
        if not result_json:
            break
        result_payload = _load_json_if_exists(Path(result_json))
        if not isinstance(result_payload, dict):
            break
        dominant_target = _dominant_bottleneck_target_from_result_payload(result_payload, cfg, policy)
        if dominant_target is None:
            break
        round_mode = str(dominant_target.get("mode", "")).strip().lower()
        round_family = str(dominant_target.get("family", "")).strip().upper()
        round_board_ids = [
            str(board_id).strip()
            for board_id in dominant_target.get("priority_board_ids", dominant_target.get("board_ids", []))
            if str(board_id).strip()
        ]
        if not round_mode or not round_board_ids:
            break
        if not board_ids:
            mode = round_mode
            family = round_family
            board_ids = round_board_ids
            streak = 1
            continue
        if round_mode != mode:
            break
        if mode == "family" and family and round_family and round_family != family:
            break
        if _board_id_overlap_ratio(board_ids, round_board_ids) < minimum_overlap_ratio:
            break
        streak += 1
    return {
        "mode": mode,
        "family": family,
        "board_ids": list(board_ids),
        "streak": streak,
    }

def _build_round_strategy_autotune_patch(
    cfg: dict,
    policy: dict,
    dominant_target: dict,
    current_param_order: List[str],
    *,
    stagnation_rounds: int,
    repeated_signature_count: int,
    priority_board_switch_streak: int,
) -> dict:
    from src.calibration.camera_calibration import CameraCalibrator  # noqa: E402
    patch: Dict[str, object] = {}

    target_board_ids = [
        str(board_id).strip()
        for board_id in dominant_target.get("board_ids", [])
        if str(board_id).strip()
    ]
    target_priority_board_ids = [
        str(board_id).strip()
        for board_id in dominant_target.get("priority_board_ids", target_board_ids)
        if str(board_id).strip()
    ]
    target_focus_board_ids = [
        str(board_id).strip()
        for board_id in dominant_target.get("focus_board_ids", target_board_ids)
        if str(board_id).strip()
    ]
    target_board_count = max(1, int(dominant_target.get("board_count", len(target_board_ids) or 1)))
    escalation_steps = max(1, target_board_count - 1)
    dominant_mode = str(dominant_target.get("mode", "")).strip().lower()
    dominant_family_name = str(dominant_target.get("family", "")).strip().upper()

    priority_cfg = cfg.get("priority_board_acceptance")
    if isinstance(priority_cfg, dict) and priority_cfg:
        priority_patch: Dict[str, object] = {}
        current_priority_board_ids = [
            str(board_id).strip()
            for board_id in priority_cfg.get("board_ids", [])
            if str(board_id).strip()
        ]
        if bool(policy.get("auto_switch_priority_boards", True)):
            if target_priority_board_ids:
                desired_priority_count = max(1, len(current_priority_board_ids) or len(target_priority_board_ids))
                desired_priority_board_ids = target_priority_board_ids[:desired_priority_count]
                minimum_switch_streak = int(policy.get("priority_board_switch_streak_rounds", 2))
                switch_allowed = (
                    not current_priority_board_ids
                    or priority_board_switch_streak >= minimum_switch_streak
                )
                if desired_priority_board_ids != current_priority_board_ids and switch_allowed:
                    priority_patch["board_ids"] = desired_priority_board_ids

        current_max_total_worsen = float(priority_cfg.get("max_total_score_worsen", 0.0))
        desired_max_total_worsen = min(
            float(policy.get("priority_max_total_worsen_cap", current_max_total_worsen)),
            current_max_total_worsen
            + float(policy.get("priority_max_total_worsen_step", 0.0)) * escalation_steps,
        )
        if desired_max_total_worsen > current_max_total_worsen:
            priority_patch["max_total_score_worsen"] = desired_max_total_worsen

        current_min_total_improvement = float(
            priority_cfg.get("min_total_board_score_improvement", 0.0)
        )
        if current_min_total_improvement > 0.0:
            desired_min_total_improvement = max(
                float(policy.get("priority_min_total_improvement_floor", 0.0)),
                current_min_total_improvement * 0.75,
            )
            if desired_min_total_improvement < current_min_total_improvement:
                priority_patch["min_total_board_score_improvement"] = desired_min_total_improvement

        current_tradeoff_ratio = float(priority_cfg.get("total_worsen_tradeoff_ratio", 0.0))
        if current_tradeoff_ratio > 0.0:
            desired_tradeoff_ratio = max(
                float(policy.get("priority_tradeoff_ratio_floor", 0.0)),
                current_tradeoff_ratio * 0.8,
            )
            if desired_tradeoff_ratio < current_tradeoff_ratio:
                priority_patch["total_worsen_tradeoff_ratio"] = desired_tradeoff_ratio

        if priority_patch:
            patch["priority_board_acceptance"] = priority_patch

    focus_cfg = cfg.get("auto_objective_board_focus")
    if isinstance(focus_cfg, dict):
        focus_patch: Dict[str, object] = {}
        focus_escalation_steps = max(
            1,
            escalation_steps,
            max(
                0,
                stagnation_rounds - int(policy.get("activation_stagnation_rounds", 2)) + 1,
            ),
        )
        current_activation_rounds = max(
            1, int(focus_cfg.get("activation_min_stagnation_rounds", 2))
        )
        desired_activation_rounds = min(
            current_activation_rounds,
            int(policy.get("force_focus_activation_rounds", 1)),
        )
        if desired_activation_rounds < current_activation_rounds:
            focus_patch["activation_min_stagnation_rounds"] = desired_activation_rounds

        current_top_k = max(1, int(focus_cfg.get("top_k", 1)))
        desired_top_k = max(current_top_k, len(target_focus_board_ids) or target_board_count)
        if desired_top_k > current_top_k:
            focus_patch["top_k"] = desired_top_k

        if bool(policy.get("restrict_to_priority_boards", True)) and not bool(
            focus_cfg.get("restrict_to_priority_boards", False)
        ):
            focus_patch["restrict_to_priority_boards"] = True

        resulting_priority_board_ids = [
            str(board_id).strip()
            for board_id in priority_patch.get("board_ids", current_priority_board_ids)
            if str(board_id).strip()
        ]
        current_focus_board_ids = [
            str(board_id).strip()
            for board_id in focus_cfg.get("focus_board_ids", [])
            if str(board_id).strip()
        ]
        focus_ids_allowed = bool(target_focus_board_ids) and (
            not bool(focus_cfg.get("restrict_to_priority_boards", False))
            or not resulting_priority_board_ids
            or all(board_id in resulting_priority_board_ids for board_id in target_focus_board_ids)
        )
        if focus_ids_allowed and current_focus_board_ids != target_focus_board_ids:
            focus_patch["focus_board_ids"] = list(target_focus_board_ids)

        current_rank_multipliers = CameraCalibrator._normalize_trial_multiplier_values(
            focus_cfg.get("rank_multipliers", [1.12, 1.08, 1.04]),
            [1.12, 1.08, 1.04],
        )
        rank_scale = 1.0 + float(policy.get("focus_rank_multiplier_step", 0.0)) * float(focus_escalation_steps)
        rank_cap = float(policy.get("focus_rank_multiplier_cap", 1.6))
        desired_rank_multipliers = [
            min(rank_cap, 1.0 + (float(value) - 1.0) * rank_scale)
            for value in current_rank_multipliers
        ]
        if len(desired_rank_multipliers) == len(current_rank_multipliers) and any(
            not math.isclose(float(desired), float(current), rel_tol=0.0, abs_tol=1e-12)
            for desired, current in zip(desired_rank_multipliers, current_rank_multipliers)
        ):
            focus_patch["rank_multipliers"] = [round(float(value), 6) for value in desired_rank_multipliers]

        current_priority_multiplier = max(
            1.0,
            float(focus_cfg.get("priority_board_multiplier", 1.02)),
        )
        desired_priority_multiplier = min(
            float(policy.get("focus_priority_multiplier_cap", 1.35)),
            current_priority_multiplier
            + float(policy.get("focus_priority_multiplier_step", 0.0)) * float(focus_escalation_steps),
        )
        if not math.isclose(
            desired_priority_multiplier,
            current_priority_multiplier,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            focus_patch["priority_board_multiplier"] = round(float(desired_priority_multiplier), 6)

        if focus_patch:
            patch["auto_objective_board_focus"] = focus_patch

    joint_cfg = cfg.get("joint_exploration")
    if isinstance(joint_cfg, dict):
        joint_patch: Dict[str, float] = {}
        current_joint_max_single_worsen = float(joint_cfg.get("max_single_score_worsen", 0.0))
        priority_patch = patch.get("priority_board_acceptance")
        target_joint_limit = current_joint_max_single_worsen
        if isinstance(priority_patch, dict) and "max_total_score_worsen" in priority_patch:
            target_joint_limit = max(
                target_joint_limit,
                float(priority_patch["max_total_score_worsen"]),
            )
        target_joint_limit = min(
            float(policy.get("joint_max_single_worsen_cap", target_joint_limit)),
            target_joint_limit,
        )
        if target_joint_limit > current_joint_max_single_worsen:
            joint_patch["max_single_score_worsen"] = target_joint_limit
        if joint_patch:
            patch["joint_exploration"] = joint_patch

    escape_cfg = cfg.get("escape_exploration")
    if isinstance(escape_cfg, dict):
        escape_patch: Dict[str, object] = {}
        current_board_focus_top_k = max(1, int(escape_cfg.get("board_focus_top_k", 1)))
        desired_board_focus_top_k = max(current_board_focus_top_k, target_board_count)
        if desired_board_focus_top_k > current_board_focus_top_k:
            escape_patch["board_focus_top_k"] = desired_board_focus_top_k
        deanchor_ready = (
            bool(policy.get("deanchor_baseline_enabled", False))
            and (
                stagnation_rounds >= int(policy.get("deanchor_activation_stagnation_rounds", 2))
                or repeated_signature_count >= int(policy.get("deanchor_repeated_signature_count", 2))
            )
        )
        if deanchor_ready and not bool(escape_cfg.get("deanchor_baseline_start", False)):
            escape_patch["deanchor_baseline_start"] = True
        if deanchor_ready:
            current_activation_rounds = max(
                1,
                int(
                    escape_cfg.get(
                        "deanchor_activation_min_stagnation_rounds",
                        policy.get("deanchor_activation_stagnation_rounds", 2),
                    )
                ),
            )
            desired_activation_rounds = min(
                current_activation_rounds,
                int(policy.get("deanchor_activation_stagnation_rounds", 2)),
            )
            if desired_activation_rounds < current_activation_rounds:
                escape_patch["deanchor_activation_min_stagnation_rounds"] = desired_activation_rounds
        if escape_patch:
            patch["escape_exploration"] = escape_patch

    unlock_priority_names: List[str] = []
    if (
        bool(policy.get("unlock_parameters_enabled", False))
        and stagnation_rounds >= int(policy.get("unlock_parameter_activation_rounds", 2))
    ):
        raw_unlock_map = policy.get("unlock_parameter_step_multipliers", {})
        family_unlock_map = (
            raw_unlock_map.get(dominant_family_name, {})
            if dominant_mode == "family" and dominant_family_name and isinstance(raw_unlock_map, dict)
            else {}
        )
        if isinstance(family_unlock_map, dict) and family_unlock_map:
            parameters_cfg = cfg.get("parameters")
            parameter_patch: Dict[str, dict] = {}
            for raw_name, raw_multiplier in family_unlock_map.items():
                name = str(raw_name).strip()
                if not name or not isinstance(parameters_cfg, dict):
                    continue
                param_cfg = parameters_cfg.get(name)
                if not isinstance(param_cfg, dict):
                    continue
                try:
                    multiplier = max(0.0, float(raw_multiplier))
                    step = max(
                        abs(float(param_cfg.get("step", 0.0))),
                        abs(float(param_cfg.get("min_step", 0.0))),
                    )
                except Exception:
                    continue
                if multiplier <= 0.0 or step <= 0.0:
                    continue
                current_bounds_multiplier = float(param_cfg.get("bounds_multiplier", _DEFAULT_BOUNDS_MULTIPLIER))
                desired_multiplier = max(multiplier, current_bounds_multiplier)
                param_patch: Dict[str, float] = {}
                if not math.isclose(desired_multiplier, current_bounds_multiplier, rel_tol=0.0, abs_tol=1e-12):
                    param_patch["bounds_multiplier"] = desired_multiplier
                if param_patch:
                    parameter_patch[name] = param_patch
                    unlock_priority_names.append(name)
            if parameter_patch:
                patch["parameters"] = parameter_patch

    desired_order = list(current_param_order)
    if current_param_order:
        existing_order = [
            str(name).strip() for name in cfg.get("optimization_order", []) if str(name).strip()
        ]
        if unlock_priority_names:
            desired_order = _merge_unique_parameter_names(unlock_priority_names, desired_order)
        if desired_order != existing_order:
            patch["optimization_order"] = list(desired_order)
    elif unlock_priority_names:
        existing_order = [
            str(name).strip() for name in cfg.get("optimization_order", []) if str(name).strip()
        ]
        desired_order = _merge_unique_parameter_names(unlock_priority_names, existing_order)
        if desired_order != existing_order:
            patch["optimization_order"] = list(desired_order)

    return patch

def _cfg_value_changed(current_value: object, desired_value: object) -> bool:
    if isinstance(current_value, (int, float)) and isinstance(desired_value, (int, float)):
        return not math.isclose(
            float(current_value),
            float(desired_value),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    return current_value != desired_value

def _apply_round_strategy_autotune_patch(cfg: dict, patch: dict) -> List[str]:
    changed_paths: List[str] = []

    optimization_order = patch.get("optimization_order")
    if isinstance(optimization_order, list):
        next_order = [str(name).strip() for name in optimization_order if str(name).strip()]
        if next_order and cfg.get("optimization_order") != next_order:
            cfg["optimization_order"] = next_order
            changed_paths.append("optimization_order")

    for section_name in (
        "priority_board_acceptance",
        "auto_objective_board_focus",
        "joint_exploration",
        "escape_exploration",
    ):
        section_patch = patch.get(section_name)
        if not isinstance(section_patch, dict) or not section_patch:
            continue
        section_cfg = cfg.get(section_name)
        if not isinstance(section_cfg, dict):
            section_cfg = {}
            cfg[section_name] = section_cfg
        for key, value in section_patch.items():
            if _cfg_value_changed(section_cfg.get(key), value):
                section_cfg[key] = value
                changed_paths.append(f"{section_name}.{key}")

    parameters_patch = patch.get("parameters")
    if isinstance(parameters_patch, dict) and parameters_patch:
        parameters_cfg = cfg.get("parameters")
        if not isinstance(parameters_cfg, dict):
            parameters_cfg = {}
            cfg["parameters"] = parameters_cfg
        for raw_name, raw_param_patch in parameters_patch.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_param_patch, dict):
                continue
            param_cfg = parameters_cfg.get(name)
            if not isinstance(param_cfg, dict):
                continue
            for key, value in raw_param_patch.items():
                if _cfg_value_changed(param_cfg.get(key), value):
                    param_cfg[key] = value
                    changed_paths.append(f"parameters.{name}.{key}")

    return changed_paths

def _maybe_autotune_round_strategy(
    config_path: Path,
    cfg: dict,
    round_summaries: List[dict],
    best_round: Optional[dict],
    *,
    current_round_index: int,
) -> Optional[dict]:
    if best_round is None:
        return None

    policy = _resolve_round_strategy_autotune_policy(cfg)
    if not bool(policy.get("enabled", True)):
        return None

    stagnation_rounds = _current_round_plateau_stagnation_count(
        round_summaries,
        float(policy.get("plateau_score_delta", 0.75)),
    )
    if stagnation_rounds < int(policy.get("activation_stagnation_rounds", 2)):
        return None

    result_json = _round_entry_result_json(best_round)
    if not result_json:
        return None

    result_payload = _load_json_if_exists(Path(result_json))
    if not isinstance(result_payload, dict):
        return None

    dominant_target = _dominant_bottleneck_target_from_result_payload(
        result_payload,
        cfg,
        policy,
    )
    if dominant_target is None:
        return None

    current_param_order = _round_entry_current_param_order(best_round)
    repeated_signature_count = _count_matching_round_signatures(
        round_summaries,
        best_round,
        float(policy.get("deanchor_score_delta", 0.05)),
    )
    priority_board_switch_state = _priority_board_target_switch_streak(round_summaries, cfg, policy)
    patch = _build_round_strategy_autotune_patch(
        cfg,
        policy,
        dominant_target,
        current_param_order,
        stagnation_rounds=stagnation_rounds,
        repeated_signature_count=repeated_signature_count,
        priority_board_switch_streak=int(priority_board_switch_state.get("streak", 0)),
    )
    if not patch:
        return None

    memory_changed_paths = _apply_round_strategy_autotune_patch(cfg, patch)
    if not memory_changed_paths:
        return None

    with open(config_path, "r", encoding="utf-8-sig") as f:
        persisted_cfg = json.load(f)

    config_changed_paths = _apply_round_strategy_autotune_patch(persisted_cfg, patch)
    if config_changed_paths:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(persisted_cfg, f, ensure_ascii=False, indent=4)

    print(
        "Auto-tuned round strategy: "
        f"path={config_path}, round={current_round_index}, mode={dominant_target.get('mode', '')}, "
        f"family={dominant_target.get('family', '')}, "
        f"boards={','.join(dominant_target['board_ids'])}, "
        f"stagnation_rounds={stagnation_rounds}, "
        f"switch_mode={priority_board_switch_state.get('mode', '')}, "
        f"switch_family={priority_board_switch_state.get('family', '')}, "
        f"switch_streak={int(priority_board_switch_state.get('streak', 0))}, "
        f"changes={', '.join(config_changed_paths or memory_changed_paths)}"
    )
    return {
        "round_index": int(current_round_index),
        "mode": str(dominant_target.get("mode", "")),
        "family": str(dominant_target.get("family", "")),
        "families": list(dominant_target.get("families", [])),
        "board_ids": list(dominant_target["board_ids"]),
        "stagnation_rounds": int(stagnation_rounds),
        "repeated_signature_count": int(repeated_signature_count),
        "priority_board_switch_mode": str(priority_board_switch_state.get("mode", "")),
        "priority_board_switch_family": str(priority_board_switch_state.get("family", "")),
        "priority_board_switch_board_ids": list(priority_board_switch_state.get("board_ids", [])),
        "priority_board_switch_streak": int(priority_board_switch_state.get("streak", 0)),
        "priority_board_switch_required_streak": int(
            policy.get("priority_board_switch_streak_rounds", 2)
        ),
        "source_result_json": result_json,
        "changed_paths": list(config_changed_paths or memory_changed_paths),
        "config_updated": bool(config_changed_paths),
        "patch": patch,
    }

def _set_run_local_script_control_result_path(run_cfg: dict, output_dir: Path) -> None:
    configured_path = str(run_cfg.get("script_control_result_path", "")).strip()
    result_name = Path(configured_path).name if configured_path else "script_control_camera_apply_result.txt"
    run_cfg["script_control_result_path"] = str((output_dir / result_name).resolve())

def _merge_unique_parameter_names(*groups: List[str]) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw_name in group:
            name = str(raw_name).strip()
            if not name or name in seen:
                continue
            merged.append(name)
            seen.add(name)
    return merged

def _resolve_escape_exploration_policy(cfg: dict) -> dict:
    default_board_param_groups = {
        "S": ["lens_fov", "pitch", "yaw", "pos_z"],
        "C": ["lens_fov", "yaw", "roll", "pos_x", "pos_y"],
    }
    default_profile_scale_map = {
        "baseline": 1.0,
        "expanded": 1.35,
        "aggressive": 1.7,
    }
    payload = cfg.get("escape_exploration")
    if not isinstance(payload, dict):
        return {
            "enabled": True,
            "coarse_start_multipliers": [8.0, 16.0, 28.0],
            "local_jitter_scale": 1.5,
            "stagnation_round_scale_up": 0.35,
            "activation_min_stagnation_rounds": 1,
            "deanchor_baseline_start": False,
            "deanchor_activation_min_stagnation_rounds": 2,
            "focus_param_budget": 4,
            "focus_rank_decay": 0.2,
            "board_focus_top_k": 2,
            "global_param_order": ["lens_fov", "pos_z", "pitch", "yaw", "pos_x", "pos_y", "roll"],
            "board_param_groups": default_board_param_groups,
            "profile_scale_map": default_profile_scale_map,
        }

    raw_coarse_multipliers = payload.get("coarse_start_multipliers", [8.0, 16.0, 28.0])
    coarse_start_multipliers: List[float] = []
    if isinstance(raw_coarse_multipliers, list):
        for raw_value in raw_coarse_multipliers:
            try:
                coarse_start_multipliers.append(max(0.0, float(raw_value)))
            except Exception:
                continue
    if not coarse_start_multipliers:
        coarse_start_multipliers = [8.0, 16.0, 28.0]

    raw_global_param_order = payload.get(
        "global_param_order",
        ["lens_fov", "pos_z", "pitch", "yaw", "pos_x", "pos_y", "roll"],
    )
    global_param_order = []
    if isinstance(raw_global_param_order, list):
        global_param_order = [str(name).strip() for name in raw_global_param_order if str(name).strip()]
    if not global_param_order:
        global_param_order = ["lens_fov", "pos_z", "pitch", "yaw", "pos_x", "pos_y", "roll"]

    board_param_groups = dict(default_board_param_groups)
    raw_board_groups = payload.get("board_param_groups")
    if isinstance(raw_board_groups, dict):
        for raw_key, raw_names in raw_board_groups.items():
            key = str(raw_key).strip().upper()
            if not key or not isinstance(raw_names, list):
                continue
            names = [str(name).strip() for name in raw_names if str(name).strip()]
            if names:
                board_param_groups[key] = names

    profile_scale_map = dict(default_profile_scale_map)
    raw_profile_scale_map = payload.get("profile_scale_map")
    if isinstance(raw_profile_scale_map, dict):
        for raw_key, raw_value in raw_profile_scale_map.items():
            key = str(raw_key).strip().lower()
            if not key:
                continue
            try:
                profile_scale_map[key] = max(0.1, float(raw_value))
            except Exception:
                continue

    return {
        "enabled": bool(payload.get("enabled", True)),
        "coarse_start_multipliers": coarse_start_multipliers,
        "local_jitter_scale": max(0.0, float(payload.get("local_jitter_scale", 1.5))),
        "stagnation_round_scale_up": max(0.0, float(payload.get("stagnation_round_scale_up", 0.35))),
        "activation_min_stagnation_rounds": max(0, int(payload.get("activation_min_stagnation_rounds", 1))),
        "deanchor_baseline_start": bool(payload.get("deanchor_baseline_start", False)),
        "deanchor_activation_min_stagnation_rounds": max(
            1,
            int(
                payload.get(
                    "deanchor_activation_min_stagnation_rounds",
                    payload.get("activation_min_stagnation_rounds", 1),
                )
            ),
        ),
        "focus_param_budget": max(1, int(payload.get("focus_param_budget", 4))),
        "focus_rank_decay": min(0.8, max(0.0, float(payload.get("focus_rank_decay", 0.2)))),
        "board_focus_top_k": max(1, int(payload.get("board_focus_top_k", 2))),
        "global_param_order": global_param_order,
        "board_param_groups": board_param_groups,
        "profile_scale_map": profile_scale_map,
    }

def _load_multi_start_guidance_from_result_json(
    result_json: Optional[str],
    strategy_payload: Optional[dict],
    default_param_priority: List[str],
    board_focus_top_k: int,
) -> Optional[dict]:
    guidance: dict = {}

    if isinstance(strategy_payload, dict):
        raw_order = strategy_payload.get("current_param_order")
        if isinstance(raw_order, list):
            guidance["param_priority"] = [
                str(name).strip() for name in raw_order if str(name).strip()
            ]
        current_profile = strategy_payload.get("current_exploration_profile")
        if isinstance(current_profile, dict):
            profile_name = str(current_profile.get("name", "")).strip().lower()
            if profile_name:
                guidance["profile_name"] = profile_name

    if result_json:
        payload = _load_json_if_exists(Path(result_json))
        if isinstance(payload, dict):
            raw_board_scores = ((payload.get("best_metrics") or {}).get("board_scores") or [])
            scored_boards: List[Tuple[float, str]] = []
            if isinstance(raw_board_scores, list):
                for raw_board_score in raw_board_scores:
                    if not isinstance(raw_board_score, dict):
                        continue
                    if not bool(raw_board_score.get("compared", False)):
                        continue
                    board_id = str(raw_board_score.get("board_id", "")).strip()
                    if not board_id:
                        continue
                    try:
                        score_value = float(raw_board_score.get("score", 0.0))
                    except Exception:
                        continue
                    scored_boards.append((score_value, board_id))
            if scored_boards:
                scored_boards.sort(key=lambda item: item[0], reverse=True)
                guidance["worst_boards"] = [
                    board_id for _, board_id in scored_boards[: max(1, int(board_focus_top_k))]
                ]
                guidance["source_result_json"] = result_json

    if "param_priority" not in guidance and default_param_priority:
        guidance["param_priority"] = list(default_param_priority)
    if not guidance:
        return None
    return guidance

def _resolve_auto_objective_board_focus_policy(cfg: dict) -> dict:
    from src.calibration.camera_calibration import CameraCalibrator  # noqa: E402
    payload = cfg.get("auto_objective_board_focus")

    if not isinstance(payload, dict):
        return {
            "enabled": False,
            "activation_min_stagnation_rounds": 2,
            "score_threshold": 10.0,
            "top_k": 3,
            "min_focus_board_count": 2,
            "restrict_to_priority_boards": False,
            "max_non_priority_top_boards": 0,
            "require_same_family": True,
            "focus_board_ids": [],
            "rank_multipliers": [1.12, 1.08, 1.04],
            "priority_board_multiplier": 1.02,
        }

    return {
        "enabled": bool(payload.get("enabled", False)),
        "activation_min_stagnation_rounds": max(
            1, int(payload.get("activation_min_stagnation_rounds", 2))
        ),
        "score_threshold": float(payload.get("score_threshold", 10.0)),
        "top_k": max(1, int(payload.get("top_k", 3))),
        "min_focus_board_count": max(
            1,
            int(
                payload.get(
                    "min_focus_board_count",
                    payload.get("min_priority_board_count", 2),
                )
            ),
        ),
        "restrict_to_priority_boards": bool(
            payload.get("restrict_to_priority_boards", False)
        ),
        "max_non_priority_top_boards": max(
            0, int(payload.get("max_non_priority_top_boards", 0))
        ),
        "require_same_family": bool(payload.get("require_same_family", True)),
        "focus_board_ids": [
            str(board_id).strip()
            for board_id in payload.get("focus_board_ids", [])
            if str(board_id).strip()
        ],
        "rank_multipliers": CameraCalibrator._normalize_trial_multiplier_values(
            payload.get("rank_multipliers", [1.12, 1.08, 1.04]),
            [1.12, 1.08, 1.04],
        ),
        "priority_board_multiplier": max(
            1.0, float(payload.get("priority_board_multiplier", 1.02))
        ),
    }

def _board_focus_family(board_id: str) -> str:
    normalized = "".join(ch for ch in str(board_id).strip().upper() if ch.isalpha())
    if normalized:
        return normalized[:1]
    board_id = str(board_id).strip().upper()
    return board_id[:1] if board_id else ""

def _build_auto_objective_board_focus_config(
    cfg: dict,
    result_json: Optional[str],
    stagnation_rounds: int,
) -> Optional[dict]:
    explicit_focus_cfg = cfg.get("objective_board_focus")
    if isinstance(explicit_focus_cfg, dict):
        return dict(explicit_focus_cfg)

    policy = _resolve_auto_objective_board_focus_policy(cfg)
    if not bool(policy.get("enabled", False)):
        return None
    if max(0, int(stagnation_rounds)) < int(policy.get("activation_min_stagnation_rounds", 2)):
        return None
    if not result_json:
        return None

    restrict_to_priority_boards = bool(policy.get("restrict_to_priority_boards", False))
    priority_accept_cfg = cfg.get("priority_board_acceptance", {})
    priority_board_ids = {
        str(board_id).strip()
        for board_id in priority_accept_cfg.get("board_ids", [])
        if str(board_id).strip()
    }
    if restrict_to_priority_boards and not priority_board_ids:
        return None

    payload = _load_json_if_exists(Path(result_json))
    if not isinstance(payload, dict):
        return None

    best_metrics = payload.get("best_metrics") or {}
    raw_board_scores = best_metrics.get("board_scores") or []
    isolated_outlier_boards = {
        str(board_id).strip()
        for board_id in (best_metrics.get("isolated_outlier_boards") or [])
        if str(board_id).strip()
    }
    if not isinstance(raw_board_scores, list):
        return None

    candidates: List[Dict[str, object]] = []
    for raw_board_score in raw_board_scores:
        if not isinstance(raw_board_score, dict):
            continue
        if not bool(raw_board_score.get("compared", False)):
            continue
        board_id = str(raw_board_score.get("board_id", "")).strip()
        if not board_id or board_id in isolated_outlier_boards:
            continue
        try:
            score_value = float(raw_board_score.get("total_score", raw_board_score.get("score", 0.0)))
        except Exception:
            continue
        if score_value < float(policy.get("score_threshold", 10.0)):
            continue
        candidates.append(
            {
                "board_id": board_id,
                "board_type": str(raw_board_score.get("board_type", "")).strip().lower(),
                "score": score_value,
            }
        )
    if not candidates:
        return None

    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    focus_candidates = candidates[: max(1, int(policy.get("top_k", 3)))]
    selected_focus = list(focus_candidates)
    configured_focus_board_ids = [
        str(board_id).strip()
        for board_id in policy.get("focus_board_ids", [])
        if str(board_id).strip()
    ]
    if configured_focus_board_ids:
        if restrict_to_priority_boards and priority_board_ids:
            configured_focus_board_ids = [
                board_id for board_id in configured_focus_board_ids if board_id in priority_board_ids
            ]
        candidate_map = {str(item["board_id"]): item for item in candidates}
        selected_focus = [
            candidate_map[board_id]
            for board_id in configured_focus_board_ids
            if board_id in candidate_map
        ]
    elif restrict_to_priority_boards:
        priority_focus = [
            item for item in focus_candidates if str(item["board_id"]) in priority_board_ids
        ]
        non_priority_count = len(focus_candidates) - len(priority_focus)
        if non_priority_count > int(policy.get("max_non_priority_top_boards", 0)):
            return None
        selected_focus = priority_focus

    if not selected_focus:
        return None

    if not configured_focus_board_ids and bool(policy.get("require_same_family", True)):
        family_groups: Dict[str, List[Dict[str, object]]] = {}
        for item in selected_focus:
            family = _board_focus_family(str(item["board_id"]))
            if not family:
                continue
            family_groups.setdefault(family, []).append(item)
        if not family_groups:
            return None
        selected_focus = max(
            family_groups.values(),
            key=lambda items: (
                len(items),
                sum(float(item["score"]) for item in items),
            ),
        )

    if len(selected_focus) < int(policy.get("min_focus_board_count", 2)):
        return None

    return {
        "enabled": True,
        "top_k": len(selected_focus),
        "score_threshold": float(policy.get("score_threshold", 10.0)),
        "rank_multipliers": list(policy.get("rank_multipliers", [1.12, 1.08, 1.04])),
        "priority_board_multiplier": float(policy.get("priority_board_multiplier", 1.02)),
        "auto_generated": True,
        "source_result_json": result_json,
        "focus_board_ids": [str(item["board_id"]) for item in selected_focus],
        "stagnation_rounds": max(0, int(stagnation_rounds)),
    }

def _resolve_multi_start_focus_params(
    policy: dict,
    guidance: Optional[dict],
    available_param_names: List[str],
) -> List[str]:
    available_set = set(available_param_names)
    guided_priority: List[str] = []
    worst_boards: List[str] = []
    if isinstance(guidance, dict):
        raw_priority = guidance.get("param_priority")
        if isinstance(raw_priority, list):
            guided_priority = [
                str(name).strip() for name in raw_priority if str(name).strip() in available_set
            ]
        raw_boards = guidance.get("worst_boards")
        if isinstance(raw_boards, list):
            worst_boards = [str(board_id).strip().upper() for board_id in raw_boards if str(board_id).strip()]

    board_param_groups = policy.get("board_param_groups", {})
    board_focus_params: List[str] = []
    for board_id in worst_boards:
        group = board_param_groups.get(board_id)
        if group is None and board_id:
            group = board_param_groups.get(board_id[:1])
        if not isinstance(group, list):
            continue
        board_focus_params.extend(
            str(name).strip() for name in group if str(name).strip() in available_set
        )

    global_param_order = [
        str(name).strip() for name in policy.get("global_param_order", []) if str(name).strip() in available_set
    ]
    return _merge_unique_parameter_names(
        board_focus_params,
        guided_priority,
        global_param_order,
        available_param_names,
    )

def _next_escape_stagnation_rounds(
    best_score: float,
    anchor_score: Optional[float],
    tolerance: float,
    previous_count: int,
) -> int:
    if anchor_score is None:
        return 0
    if float(best_score) <= float(anchor_score) - float(tolerance):
        return 0
    return max(0, int(previous_count)) + 1



def _compute_auto_jitter(camera_name: str, project_root: Optional[Path] = None) -> float:
    """Compute adaptive multi-start jitter from history or fallback 2.0."""
    _prj = project_root or Path.cwd()
    summary_path = _camera_history_summary_path(camera_name, _prj)
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        scores = [float(r.get("final_score", 0)) for r in summary.get("runs", [])]
        if len(scores) >= 3:
            sigma = np.std(scores)
            if sigma > 0:
                return max(0.3, min(6.0, sigma * 0.05))
    pool = _load_params_pool(camera_name, _prj)
    pool_entries = pool.get("entries", {})
    if len(pool_entries) >= 3:
        pool_scores = [float(e.get("best_score", 0)) for e in pool_entries.values()]
        sigma = np.std(pool_scores)
        if sigma > 0:
            return max(0.3, min(6.0, sigma * 0.05))
    return 2.0

def _build_multi_start_run_configs(
    cfg: dict,
    base_output_dir: Path,
    camera_name: str,
    start_count: int,
    jitter_steps: float,
    seed: int,
    max_iters_override: Optional[int],
    output_root_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> Tuple[Path, List[dict]]:
    if start_count <= 0:
        raise ValueError("multi-start count must be positive")

    js = _compute_auto_jitter(camera_name, project_root=project_root) if str(jitter_steps).lower() == "auto" else float(jitter_steps)
    base_parameters = cfg.get("parameters")
    if not isinstance(base_parameters, dict) or not base_parameters:
        raise ValueError("parameters must be a non-empty object for multi-start mode")

    root_output_dir = output_root_dir or _build_isolated_output_dir(
        "multistart", camera_parent=camera_name, project_root=project_root
    )
    rng = random.Random(seed)
    run_cfgs: List[dict] = []
    escape_policy = _resolve_escape_exploration_policy(cfg)
    raw_multi_start_guidance = cfg.get("multi_start_guidance")
    multi_start_guidance = dict(raw_multi_start_guidance) if isinstance(raw_multi_start_guidance, dict) else {}
    available_param_names = [str(name) for name in base_parameters.keys()]
    focus_param_order = _resolve_multi_start_focus_params(
        escape_policy,
        multi_start_guidance,
        available_param_names,
    )
    focus_param_budget = min(len(focus_param_order), int(escape_policy.get("focus_param_budget", 4)))
    focus_param_ranks = {
        name: index for index, name in enumerate(focus_param_order[:focus_param_budget])
    }
    guidance_profile_name = str(multi_start_guidance.get("profile_name", "baseline")).strip().lower()
    profile_scale_map = escape_policy.get("profile_scale_map", {})
    profile_scale = float(profile_scale_map.get(guidance_profile_name, 1.0))
    stagnation_rounds = max(0, int(multi_start_guidance.get("stagnation_rounds", 0)))
    round_escape_scale = 1.0 + (
        float(escape_policy.get("stagnation_round_scale_up", 0.35)) * float(stagnation_rounds)
    )
    coarse_start_multipliers = [
        max(0.0, float(multiplier)) * profile_scale * round_escape_scale
        for multiplier in escape_policy.get("coarse_start_multipliers", [8.0, 16.0, 28.0])
    ]
    if not coarse_start_multipliers:
        coarse_start_multipliers = [max(1.0, float(js) or 1.0)]
    coarse_escape_active = (
        bool(escape_policy.get("enabled", False))
        and stagnation_rounds >= int(escape_policy.get("activation_min_stagnation_rounds", 1))
    )
    deanchor_baseline_start = (
        coarse_escape_active
        and bool(escape_policy.get("deanchor_baseline_start", False))
        and stagnation_rounds >= int(escape_policy.get("deanchor_activation_min_stagnation_rounds", 2))
    )
    deanchor_multiplier_index = min(
        max(0, stagnation_rounds - int(escape_policy.get("deanchor_activation_min_stagnation_rounds", 2))),
        max(0, len(coarse_start_multipliers) - 1),
    )

    for start_index in range(start_count):
        run_cfg = copy.deepcopy(cfg)
        run_output_dir = root_output_dir / f"explore_{start_index:02d}"
        run_cfg["output_dir"] = str(run_output_dir)
        _set_run_local_script_control_result_path(run_cfg, run_output_dir)
        if max_iters_override is not None:
            run_cfg["max_iters"] = int(max_iters_override)

        run_parameters: Dict[str, dict] = {}
        initial_values: Dict[str, float] = {}
        escape_variant: Optional[dict] = None
        if deanchor_baseline_start and start_index == 0:
            escape_variant = {
                "mode": "focused_escape",
                "coarse_multiplier": coarse_start_multipliers[deanchor_multiplier_index],
            }
        elif coarse_escape_active and start_index > 0:
            escape_index = start_index - 1
            escape_variant = {
                "mode": ["coarse_positive", "coarse_negative", "focused_escape"][escape_index % 3],
                "coarse_multiplier": coarse_start_multipliers[
                    min(escape_index // 3, len(coarse_start_multipliers) - 1)
                ],
            }

        for name, base_param in base_parameters.items():
            initial_value = float(base_param["initial"])
            min_value, max_value = _resolve_parameter_bounds(base_param)

            step = abs(float(base_param.get("step", 0.0)))
            decimals = int(base_param.get("decimals", 4))
            start_value = initial_value
            unlocked = not math.isclose(min_value, max_value, rel_tol=0.0, abs_tol=1e-12)
            if (start_index > 0 or escape_variant is not None) and unlocked and step > 0.0:
                delta = 0.0
                if js > 0.0:
                    delta += (
                        rng.uniform(-js, js)
                        * step
                        * max(1.0, float(escape_policy.get("local_jitter_scale", 1.0)))
                    )
                if escape_variant is not None:
                    focus_rank = focus_param_ranks.get(name)
                    if focus_rank is not None:
                        focus_rank_decay = float(escape_policy.get("focus_rank_decay", 0.2))
                        rank_scale = max(0.35, 1.0 - (focus_rank * focus_rank_decay))
                        if escape_variant["mode"] == "focused_escape":
                            direction = 1.0 if ((start_index + focus_rank) % 2 == 0) else -1.0
                            delta += direction * float(escape_variant["coarse_multiplier"]) * step * rank_scale * 0.75
                        else:
                            base_direction = 1.0 if escape_variant["mode"] == "coarse_positive" else -1.0
                            direction = base_direction if focus_rank % 2 == 0 else -base_direction
                            delta += direction * float(escape_variant["coarse_multiplier"]) * step * rank_scale
                start_value = float(np.clip(initial_value + delta, min_value, max_value))
            start_value = _quantize_float(start_value, decimals)

            run_param = _build_explicit_parameter_config(base_param, start_value)
            run_parameters[name] = run_param
            initial_values[name] = start_value

        run_cfg["parameters"] = run_parameters
        multi_start_meta = {
            "index": start_index,
            "seed": seed,
            "jitter_steps": jitter_steps,
            "initial_values": initial_values,
        }
        if escape_variant is not None:
            multi_start_meta.update(
                {
                    "strategy": str(escape_variant["mode"]),
                    "coarse_step_multiplier": float(escape_variant["coarse_multiplier"]),
                    "focus_params": focus_param_order[:focus_param_budget],
                    "focus_boards": list(multi_start_guidance.get("worst_boards", [])),
                    "profile_name": guidance_profile_name,
                    "stagnation_rounds": stagnation_rounds,
                    "baseline_deanchored": bool(deanchor_baseline_start and start_index == 0),
                }
            )
        run_cfg["multi_start"] = multi_start_meta
        run_cfgs.append(run_cfg)

    return root_output_dir, run_cfgs



class MultiStartSharedState:
    """State shared across multi-start runs, merged via exponential moving average."""
    def __init__(self):
        self.step_scales: Dict[str, float] = {}
        self.preferred_directions: Dict[str, float] = {}
        self.priority_scores: Dict[str, float] = {}
        self.best_per_board_scores: Dict[str, float] = {}

    def merge(self, start_state: dict, alpha: float = 0.3):
        for name, scale in start_state.get("step_scales", {}).items():
            if isinstance(scale, (int, float)):
                prev = self.step_scales.get(name, scale)
                self.step_scales[name] = alpha * scale + (1.0 - alpha) * prev
        for name, direction in start_state.get("preferred_directions", {}).items():
            if isinstance(direction, (int, float)):
                prev = self.preferred_directions.get(name, direction)
                self.preferred_directions[name] = alpha * direction + (1.0 - alpha) * prev
        for name, score in start_state.get("priority_scores", {}).items():
            if isinstance(score, (int, float)):
                prev = self.priority_scores.get(name, score)
                self.priority_scores[name] = alpha * score + (1.0 - alpha) * prev
        for bid, score in start_state.get("best_per_board_scores", {}).items():
            if isinstance(score, (int, float)):
                prev = self.best_per_board_scores.get(bid, score)
                self.best_per_board_scores[bid] = min(prev, score)

    def to_dict(self) -> dict:
        return {
            "step_scales": dict(self.step_scales),
            "preferred_directions": dict(self.preferred_directions),
            "priority_scores": dict(self.priority_scores),
            "best_per_board_scores": dict(self.best_per_board_scores),
        }

def _run_multi_start_campaign(
    config_path: Path,
    cfg: dict,
    base_output_dir: Path,
    camera_name: str,
    start_count: int,
    jitter_steps: float,
    seed: int,
    max_iters_override: Optional[int],
    output_root_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
    *,
    round_index: int = 0,
    round_count: int = 0,
    overall_total_iters: int = 0,
    ) -> dict:
    from src.calibration.camera_calibration import CameraCalibrator  # noqa: E402
    root_output_dir, run_cfgs = _build_multi_start_run_configs(

        cfg,
        base_output_dir=base_output_dir,
        camera_name=camera_name,
        start_count=start_count,
        jitter_steps=jitter_steps,
        seed=seed,
        max_iters_override=max_iters_override,
        output_root_dir=output_root_dir,
        project_root=project_root,
    )
    root_output_dir.mkdir(parents=True, exist_ok=True)

    print(
        "Multi-start campaign: "
        f"runs={start_count}, "
        f"max_iters={run_cfgs[0].get('max_iters')}, "
        f"jitter_steps={jitter_steps}, "
        f"seed={seed}, "
        f"output_dir={root_output_dir}"
    )

    run_summaries: List[dict] = []
    for run_cfg in run_cfgs:
        multi_cfg = run_cfg.get("multi_start", {})
        start_index = int(multi_cfg.get("index", 0))
        output_dir = Path(run_cfg["output_dir"])
        live_log_path = _configure_live_log(run_cfg, False, project_root=project_root)
        initial_values = multi_cfg.get("initial_values", {})
        strategy_label = str(multi_cfg.get("strategy", "baseline")).strip()
        focus_boards = multi_cfg.get("focus_boards") or []
        focus_params = multi_cfg.get("focus_params") or []
        meta_suffix = ""
        if strategy_label and strategy_label != "baseline":
            meta_suffix = (
                f" strategy={strategy_label}"
                f" coarse_step_multiplier={float(multi_cfg.get('coarse_step_multiplier', 0.0)):.2f}"
                f" focus_boards={focus_boards}"
                f" focus_params={focus_params}"
            )
        print(
            f"Multi-start run {start_index + 1}/{start_count}: "
            f"output_dir={output_dir} "
            f"initials={_format_scalar_value_map(initial_values)}{meta_suffix}"
        )

        calib = CameraCalibrator(run_cfg, config_path=config_path)
        calib.live_log_path = live_log_path
        calib.print_progress_json = True
        calib._calib_phase = "explore"
        calib._calib_dir_index = start_index
        calib._calib_total_dirs = start_count
        calib._calib_max_iters = int(run_cfg.get("max_iters", 0))
        calib._calib_round_index = round_index
        calib._calib_round_count = round_count
        calib._calib_overall_total_iters = overall_total_iters
        try:
            result = calib.optimize()
            run_summaries.append(
                {
                    "start_index": start_index,
                    "status": "finished",
                    "output_dir": str(output_dir),
                    "live_log": str(live_log_path),
                    "initial_values": initial_values,
                    "best_score": result["best_score"],
                    "best_values": result["best_values"],
                    "best_image": result["best_image"],
                    "acceptance": result.get("acceptance"),
                    "best_score_image": result.get("best_score_image"),
                    "best_overlay_image": result.get("best_overlay_image"),
                    "result_json": str(output_dir / "result.json"),
                }
            )
            print(
                f"Multi-start run {start_index + 1} finished: "
                f"best_score={result['best_score']:.6f}"
            )
        except Exception as exc:
            run_summaries.append(
                {
                    "start_index": start_index,
                    "status": "failed",
                    "output_dir": str(output_dir),
                    "live_log": str(live_log_path),
                    "initial_values": initial_values,
                    "error": str(exc),
                }
            )
            print(f"Multi-start run {start_index + 1} failed: {exc}")
            if _is_fatal_initial_board_error(exc):
                raise

    successful_runs = [entry for entry in run_summaries if entry.get("status") == "finished"]
    successful_runs.sort(key=lambda item: float(item["best_score"]))
    best_run = successful_runs[0] if successful_runs else None
    summary = {
        "config": str(config_path),
        "output_dir": str(root_output_dir),
        "start_count": start_count,
        "max_iters": run_cfgs[0].get("max_iters"),
        "jitter_steps": jitter_steps,
        "seed": seed,
        "best_run": best_run,
        "runs": run_summaries,
    }
    summary_path = root_output_dir / "multistart_summary.json"
    summary_path.write_text(json.dumps(_round_floats(summary, skip_keys={"best_values", "initial_values"}), ensure_ascii=False, indent=2), encoding="utf-8")

    if best_run is None:
        raise RuntimeError(f"All multi-start runs failed. See {summary_path}")

    print(f"Multi-start summary: {summary_path}")
    print(
        "Multi-start best: "
        f"start_index={best_run['start_index']} "
        f"best_score={float(best_run['best_score']):.6f} "
        f"output_dir={best_run['output_dir']}"
    )
    return summary

def _cfg_with_initial_values(cfg: dict, initial_values: Dict[str, float]) -> dict:
    run_cfg = copy.deepcopy(cfg)
    parameters = run_cfg.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("parameters must be a non-empty object")

    for name, param_cfg in list(parameters.items()):
        if not isinstance(param_cfg, dict):
            continue
        next_initial = float(initial_values.get(name, param_cfg.get("initial", 0.0)))
        parameters[name] = _build_explicit_parameter_config(param_cfg, next_initial)
    return run_cfg

def _extract_initial_values_from_cfg(cfg: dict) -> Dict[str, float]:
    parameters = cfg.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("parameters must be a non-empty object")

    initial_values: Dict[str, float] = {}
    for name, param_cfg in parameters.items():
        if not isinstance(param_cfg, dict) or "initial" not in param_cfg:
            continue
        initial_values[str(name)] = float(param_cfg["initial"])
    return initial_values

def _resolve_round_seed_policy(cfg: dict) -> dict:
    payload = cfg.get("round_seeding")
    if not isinstance(payload, dict):
        return {
            "enabled": False,
            "prefer_history_best": True,
            "carry_forward_only_if_improved": True,
            "score_tolerance": 1e-6,
        }

    return {
        "enabled": bool(payload.get("enabled", False)),
        "prefer_history_best": bool(payload.get("prefer_history_best", True)),
        "carry_forward_only_if_improved": bool(payload.get("carry_forward_only_if_improved", True)),
        "score_tolerance": float(payload.get("score_tolerance", 1e-6)),
    }

def _resolve_round_seed_anchor(
    config_path: Path,
    camera_name: str,
    cfg: dict,
    policy: dict,
) -> Tuple[Dict[str, float], Optional[float], str]:
    anchor_values: Dict[str, float] = {}
    anchor_score: Optional[float] = None
    anchor_source = "live_read"

    # Use config initial values (from DDE vehicle-file read)
    config_initial = _extract_initial_values_from_cfg(cfg)
    if config_initial:
        print(f"Using config initial values as seed anchor")
        return config_initial, anchor_score, "config_initial"

    # Fall back to live read (empty anchor)
    return anchor_values, anchor_score, anchor_source

def _choose_next_round_seed_values(
    best_score: float,
    best_values: Dict[str, float],
    *,
    policy: dict,
    anchor_values: Dict[str, float],
    anchor_score: Optional[float],
    anchor_source: str,
) -> Tuple[Dict[str, float], Optional[float], str]:
    if not bool(policy.get("enabled", False)):
        return dict(best_values), float(best_score), "round_best"

    tolerance = float(policy.get("score_tolerance", 1e-6))
    carry_forward_only_if_improved = bool(policy.get("carry_forward_only_if_improved", True))
    candidate_values = dict(best_values)
    candidate_score = float(best_score)

    if (
        carry_forward_only_if_improved
        and anchor_values
        and anchor_score is not None
        and candidate_score > anchor_score + tolerance
    ):
        print(
            "Round seed guard: "
            f"keeping {anchor_source} for next round because round_best={candidate_score:.6f} "
            f"is worse than anchor_score={anchor_score:.6f}"
        )
        return dict(anchor_values), anchor_score, anchor_source

    return candidate_values, candidate_score, "round_best"

def _select_campaign_best_run(explore_best_run: dict, refine_run: dict) -> dict:
    explore_candidate = dict(explore_best_run)
    explore_candidate["stage"] = "explore"
    refine_candidate = dict(refine_run)
    refine_candidate["stage"] = "refine"
    if float(refine_candidate["best_score"]) < float(explore_candidate["best_score"]):
        return refine_candidate
    return explore_candidate

def _should_skip_refine_run(
    cfg: dict,
    best_run: dict,
    *,
    previous_escape_stagnation_rounds: int,
    anchor_score: Optional[float],
    round_seed_policy: dict,
) -> Optional[dict]:
    policy = _resolve_round_strategy_autotune_policy(cfg)
    if not bool(policy.get("skip_refine_on_plateau", False)):
        return None
    if anchor_score is None:
        return None

    try:
        explore_best_score = float(best_run["best_score"])
    except Exception:
        return None

    projected_stagnation_rounds = _next_escape_stagnation_rounds(
        explore_best_score,
        anchor_score,
        float(round_seed_policy.get("score_tolerance", 1e-6)),
        previous_escape_stagnation_rounds,
    )
    if projected_stagnation_rounds < int(policy.get("skip_refine_activation_stagnation_rounds", 2)):
        return None
    if explore_best_score < float(anchor_score) - float(policy.get("skip_refine_score_delta", 0.05)):
        return None

    return {
        "skipped": True,
        "reason": (
            "refine skipped because explore best stayed inside the current plateau basin"
        ),
        "anchor_score": float(anchor_score),
        "explore_best_score": explore_best_score,
        "projected_stagnation_rounds": int(projected_stagnation_rounds),
    }

def _run_explore_then_refine_campaign(
    config_path: Path,
    cfg: dict,
    base_output_dir: Path,
    camera_name: str,
    start_count: int,
    jitter_steps: float,
    seed: int,
    explore_max_iters: int,
    refine_max_iters: Optional[int],
    previous_escape_stagnation_rounds: int = 0,
    anchor_score: Optional[float] = None,
    output_root_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
    *,
    round_index: int = 0,
    round_count: int = 0,
    overall_total_iters: int = 0,
    ) -> dict:
    from src.calibration.camera_calibration import CameraCalibrator  # noqa: E402
    if start_count <= 0:

        raise ValueError("explore-then-refine mode requires a positive start count")
    if explore_max_iters < 0:
        raise ValueError("explore-then-refine mode requires positive explore iterations")

    round_dir = output_root_dir or _build_isolated_output_dir(
        "round", camera_parent=camera_name, project_root=project_root
    )
    round_dir.mkdir(parents=True, exist_ok=True)

    print(
        "Explore-then-refine campaign: "
        f"explore_runs={start_count}, "
        f"explore_iters={explore_max_iters}, "
        f"refine_iters={refine_max_iters or int(cfg.get('max_iters', 0))}, "
        f"jitter_steps={jitter_steps}, "
        f"seed={seed}, "
        f"round_dir={round_dir}"
    )

    explore_summary = _run_multi_start_campaign(
        config_path=config_path,
        cfg=cfg,
        base_output_dir=base_output_dir,
        camera_name=camera_name,
        start_count=start_count,
        jitter_steps=jitter_steps,
        seed=seed,
        max_iters_override=explore_max_iters,
        output_root_dir=round_dir,
        round_index=round_index,
        round_count=round_count,
        overall_total_iters=overall_total_iters,
        project_root=project_root,
    )
    best_run = explore_summary["best_run"]
    best_values = dict(best_run["best_values"])
    round_seed_policy = _resolve_round_seed_policy(cfg)
    skip_refine_payload = _should_skip_refine_run(
        cfg,
        best_run,
        previous_escape_stagnation_rounds=previous_escape_stagnation_rounds,
        anchor_score=anchor_score,
        round_seed_policy=round_seed_policy,
    )
    if skip_refine_payload is not None:
        summary = {
            "config": str(config_path),
            "campaign_output_dir": str(round_dir),
            "explore": {
                "output_dir": str(explore_summary["output_dir"]),
                "summary_json": str(Path(explore_summary["output_dir"]) / "multistart_summary.json"),
                "start_count": start_count,
                "max_iters": explore_max_iters,
            "jitter_steps": js,
                "seed": seed,
                "best_run": best_run,
            },
            "refine": {
                "skipped": True,
                "reason": skip_refine_payload["reason"],
                "max_iters": int(refine_max_iters or int(cfg.get("max_iters", 0))),
                "anchor_score": skip_refine_payload["anchor_score"],
                "explore_best_score": skip_refine_payload["explore_best_score"],
                "projected_stagnation_rounds": skip_refine_payload["projected_stagnation_rounds"],
            },
        }
        summary["best_run"] = dict(best_run)
        summary["best_run"]["stage"] = "explore"
        summary_path = round_dir / "campaign_summary.json"
        summary_path.write_text(json.dumps(_round_floats(summary, skip_keys={"best_values", "seed_values"}), ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            "Refine skipped: "
            f"explore_best_score={skip_refine_payload['explore_best_score']:.6f} "
            f"anchor_score={skip_refine_payload['anchor_score']:.6f} "
            f"projected_stagnation_rounds={skip_refine_payload['projected_stagnation_rounds']}"
        )
        print("Campaign summary:", summary_path)
        print("Campaign best stage:", summary["best_run"]["stage"])
        print("Campaign best score:", summary["best_run"]["best_score"])
        print("Campaign best image:", summary["best_run"]["best_image"])
        print("Campaign best result JSON:", summary["best_run"]["result_json"])
        return summary

    refine_cfg = _cfg_with_initial_values(cfg, best_values)
    refine_output_dir = round_dir / "refine"
    refine_cfg["output_dir"] = str(refine_output_dir)
    _set_run_local_script_control_result_path(refine_cfg, refine_output_dir)
    if refine_max_iters is not None:
        refine_cfg["max_iters"] = int(refine_max_iters)

    live_log_path = _configure_live_log(refine_cfg, False, project_root=project_root)
    print(
        "Refine run: "
        f"source_start_index={best_run['start_index']}, "
        f"output_dir={refine_output_dir}, "
        f"initials={_format_scalar_value_map(best_values)}"
    )

    calib = CameraCalibrator(refine_cfg)
    calib.live_log_path = live_log_path
    calib.print_progress_json = True
    calib._calib_phase = "refine"
    calib._calib_max_iters = int(refine_cfg.get("max_iters", 0))
    calib._calib_round_index = round_index
    calib._calib_round_count = round_count
    calib._calib_overall_total_iters = overall_total_iters
    result = calib.optimize()

    summary = {
        "config": str(config_path),
        "campaign_output_dir": str(round_dir),
        "explore": {
            "output_dir": str(explore_summary["output_dir"]),
            "summary_json": str(Path(explore_summary["output_dir"]) / "multistart_summary.json"),
            "start_count": start_count,
            "max_iters": explore_max_iters,
            "jitter_steps": jitter_steps,
            "seed": seed,
            "best_run": best_run,
        },
        "refine": {
            "output_dir": str(refine_output_dir),
            "live_log": str(live_log_path),
            "max_iters": int(refine_cfg.get("max_iters", 0)),
            "seed_values": best_values,
            "source_start_index": best_run["start_index"],
            "best_score": result["best_score"],
            "best_values": result["best_values"],
            "best_image": result["best_image"],
            "acceptance": result.get("acceptance"),
            "best_score_image": result.get("best_score_image"),
            "best_overlay_image": result.get("best_overlay_image"),
            "result_json": str(refine_output_dir / "result.json"),
        },
    }
    summary["best_run"] = _select_campaign_best_run(best_run, summary["refine"])
    summary_path = round_dir / "campaign_summary.json"
    summary_path.write_text(json.dumps(_round_floats(summary, skip_keys={"best_values", "seed_values"}), ensure_ascii=False, indent=2), encoding="utf-8")

    best_run_overall = summary["best_run"]
    print("Campaign summary:", summary_path)
    print("Campaign best stage:", best_run_overall["stage"])
    print("Campaign best score:", best_run_overall["best_score"])
    print("Campaign best image:", best_run_overall["best_image"])
    print("Campaign best result JSON:", best_run_overall["result_json"])
    return summary

def _load_strategy_adaptation_from_result_json(result_json: Optional[str]) -> Optional[dict]:
    if not result_json:
        return None
    payload = _load_json_if_exists(Path(result_json))
    if not isinstance(payload, dict):
        return None
    strategy_payload = payload.get("strategy_adaptation")
    if not isinstance(strategy_payload, dict):
        return None
    return dict(strategy_payload)

def _cfg_with_round_guidance(
    cfg: dict,
    best_values: Dict[str, float],
    strategy_payload: Optional[dict],
    *,
    result_json: Optional[str] = None,
    escape_stagnation_rounds: int = 0,
) -> dict:
    next_cfg = _cfg_with_initial_values(cfg, best_values)
    current_params = next_cfg.get("parameters") or {}
    guided_order: List[str] = []
    seen: set[str] = set()
    if isinstance(strategy_payload, dict):
        raw_order = strategy_payload.get("current_param_order")
        if isinstance(raw_order, list):
            for raw_name in raw_order:
                name = str(raw_name).strip()
                if not name or name in seen or name not in current_params:
                    continue
                guided_order.append(name)
                seen.add(name)
    for name in current_params.keys():
        if name not in seen:
            guided_order.append(name)

    if guided_order:
        next_cfg["optimization_order"] = guided_order

    objective_board_focus_cfg = _build_auto_objective_board_focus_config(
        next_cfg,
        result_json,
        escape_stagnation_rounds,
    )
    if objective_board_focus_cfg is None:
        next_cfg.pop("objective_board_focus", None)
    else:
        next_cfg["objective_board_focus"] = objective_board_focus_cfg

    escape_policy = _resolve_escape_exploration_policy(next_cfg)
    if not bool(escape_policy.get("enabled", False)):
        next_cfg.pop("multi_start_guidance", None)
        return next_cfg

    multi_start_guidance = _load_multi_start_guidance_from_result_json(
        result_json,
        strategy_payload if isinstance(strategy_payload, dict) else None,
        guided_order,
        int(escape_policy.get("board_focus_top_k", 2)),
    ) or {"param_priority": guided_order}
    multi_start_guidance["stagnation_rounds"] = max(0, int(escape_stagnation_rounds))
    if guided_order:
        multi_start_guidance["param_priority"] = guided_order
    next_cfg["multi_start_guidance"] = multi_start_guidance
    return next_cfg

def _write_rounds_summary(rounds_root: Path, payload: dict) -> Path:
    rounds_root.mkdir(parents=True, exist_ok=True)
    summary_path = rounds_root / "rounds_summary.json"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path

def _is_fatal_initial_board_error(exc: Exception) -> bool:
    return str(exc).startswith("Initial evaluation aborted due to fatal board scores:")

def _is_timeout_like_failure_text(text: str) -> bool:
    lowered = str(text).strip().lower()
    return any(marker in lowered for marker in _DDE_RECOVERY_ERROR_MARKERS)

def _extract_failure_summary_path(text: str) -> Optional[Path]:
    raw_text = str(text).strip()
    marker = " See "
    if marker not in raw_text:
        return None
    _, _, path_text = raw_text.partition(marker)
    candidate = path_text.strip()
    if not candidate:
        return None
    return Path(candidate)

def _is_timeout_like_round_failure(exc: Exception) -> bool:
    text = str(exc)
    if _is_timeout_like_failure_text(text):
        return True

    summary_path = _extract_failure_summary_path(text)
    if summary_path is None or not summary_path.exists():
        return False

    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return False

    runs = payload.get("runs")
    if not isinstance(runs, list):
        return False

    failed_errors = [
        str(entry.get("error", ""))
        for entry in runs
        if isinstance(entry, dict) and entry.get("status") == "failed"
    ]
    return bool(failed_errors) and all(
        _is_timeout_like_failure_text(error_text) for error_text in failed_errors
    )

def _run_single_optimize(
    config_path: Path,
    cfg: dict,
    base_output_dir: Path,
    camera_name: str,
    project_root: Optional[Path] = None,
    *,
    resume_from_result: bool,
    output_dir_override: Optional[Path] = None,
    round_index: int = 0,
    ) -> dict:
    from src.calibration.camera_calibration import CameraCalibrator  # noqa: E402
    run_cfg = copy.deepcopy(cfg)

    marker_path = _marker_path_for_output_dir(base_output_dir, project_root=project_root)
    resume_result_path: Optional[Path] = None
    if resume_from_result:
        resume_result_path = _read_latest_result_path(marker_path, base_output_dir)

    if output_dir_override is not None:
        run_cfg["output_dir"] = str(output_dir_override)
    else:
        run_cfg["output_dir"] = str(
            _build_isolated_output_dir("run", camera_parent=camera_name, project_root=project_root)
        )

    marker_payload = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config": str(config_path),
        "base_output_dir": str(base_output_dir),
        "output_dir": str(run_cfg["output_dir"]),
        "max_iters": int(run_cfg.get("max_iters", 0)),
        "resume_from_result": bool(resume_from_result),
        "status": "starting",
    }
    _write_run_marker(marker_path, marker_payload)

    live_log_path = _configure_live_log(run_cfg, resume_from_result)
    print("Live log:", str(live_log_path))
    print("Isolated output dir:", str(run_cfg["output_dir"]))

    marker_payload["status"] = "running"
    marker_payload["live_log"] = str(live_log_path)
    _write_run_marker(marker_path, marker_payload)

    calib = CameraCalibrator(run_cfg, config_path=config_path)
    calib.live_log_path = live_log_path
    calib.print_progress_json = True
    calib._calib_round_index = round_index
    try:
        if resume_from_result:
            calib.load_best_values_from_result(
                resume_result_path or (base_output_dir / "result.json")
            )

        result = calib.optimize()
        _write_best_values_to_vehicle_config(
            config_path,
            run_cfg,
            camera_name,
            float(result["best_score"]),
            result["best_values"],
            project_root=project_root,
        )
        marker_payload.update(
            {
                "status": "finished",
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "best_score": result["best_score"],
                "best_values": result["best_values"],
                "best_image": result["best_image"],
                "result_json": str(Path(run_cfg["output_dir"]) / "result.json"),
                "run_session_id": result.get("run_session_id"),
            }
        )
        _write_run_marker(marker_path, marker_payload)
        return {
            "output_dir": str(run_cfg["output_dir"]),
            "live_log": str(live_log_path),
            "result": result,
            "result_json": str(Path(run_cfg["output_dir"]) / "result.json"),
        }
    except Exception as exc:
        marker_payload.update(
            {
                "status": "failed",
                "failed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "error": str(exc),
            }
        )
        _write_run_marker(marker_path, marker_payload)
        raise

def _run_plain_optimize_rounds(
    config_path: Path,
    cfg: dict,
    base_output_dir: Path,
    camera_name: str,
    round_count: int,
    resume_from_result: bool,
    project_root: Optional[Path] = None,
) -> dict:
    if round_count <= 0:
        raise ValueError("round_count must be positive")

    rounds_root = _build_isolated_output_dir("rounds", camera_parent=camera_name, project_root=project_root)
    active_cfg = copy.deepcopy(cfg)
    target_score = float(cfg.get("target_score", 5.0))
    round_seed_policy = _resolve_round_seed_policy(cfg)
    anchor_values, anchor_score, anchor_source = _resolve_round_seed_anchor(
        config_path,
        camera_name,
        cfg,
        round_seed_policy,
    )
    if round_seed_policy["enabled"] or round_seed_policy.get("prefer_history_best", True):
        active_cfg = _cfg_with_initial_values(active_cfg, anchor_values)
        verify_params = active_cfg.get("parameters", {})
        anchor_mismatches = []
        for name, expected in anchor_values.items():
            actual = float(verify_params.get(name, {}).get("initial", float("nan")))
            if abs(actual - expected) > 1e-9:
                anchor_mismatches.append(f"{name}: expected={expected}, actual={actual}")
        if anchor_mismatches:
            print(
                f"ANCHOR APPLY MISMATCH: camera={camera_name}, source={anchor_source}, "
                f"score={anchor_score}, mismatches={anchor_mismatches}"
            )
        else:
            print(
                f"Anchor applied: camera={camera_name}, source={anchor_source}, "
                f"score={anchor_score}, values={_format_scalar_value_map(anchor_values)}, verify=OK"
            )
    round_summaries: List[dict] = []
    best_round: Optional[dict] = None
    escape_stagnation_rounds = 0
    escape_tolerance = float(round_seed_policy.get("score_tolerance", 1e-6))
    strategy_autotune_events: List[dict] = []

    for round_index in range(round_count):
        round_no = round_index + 1
        round_output_dir = rounds_root / f"round_{round_no:02d}"
        print(
            f"Plain optimize rounds: round={round_no}/{round_count} "
            f"output_dir={round_output_dir}"
        )
        run_payload = _run_single_optimize(
            config_path=config_path,
            cfg=active_cfg,
            base_output_dir=base_output_dir,
            camera_name=camera_name,
            resume_from_result=resume_from_result and round_index == 0,
            output_dir_override=round_output_dir,
            round_index=round_no,
            project_root=project_root,
        )
        result = dict(run_payload["result"])
        strategy_payload = result.get("strategy_adaptation")
        current_param_order: List[str] = []
        if isinstance(strategy_payload, dict):
            raw_order = strategy_payload.get("current_param_order")
            if isinstance(raw_order, list):
                current_param_order = [str(item) for item in raw_order]
        round_entry = {
            "round_index": round_no,
            "output_dir": run_payload["output_dir"],
            "live_log": run_payload["live_log"],
            "best_score": result["best_score"],
            "best_values": result["best_values"],
            "best_image": result["best_image"],
            "result_json": run_payload["result_json"],
            "current_param_order": current_param_order,
        }
        round_summaries.append(round_entry)
        if best_round is None or float(round_entry["best_score"]) < float(best_round["best_score"]):
            best_round = round_entry
        escape_stagnation_rounds = _next_escape_stagnation_rounds(
            float(result["best_score"]),
            anchor_score,
            escape_tolerance,
            escape_stagnation_rounds,
        )
        next_seed_values, next_anchor_score, next_seed_source = _choose_next_round_seed_values(
            float(result["best_score"]),
            dict(result["best_values"]),
            policy=round_seed_policy,
            anchor_values=anchor_values,
            anchor_score=anchor_score,
            anchor_source=anchor_source,
        )
        active_cfg = _cfg_with_round_guidance(
            active_cfg,
            next_seed_values,
            strategy_payload if isinstance(strategy_payload, dict) else None,
            result_json=run_payload["result_json"],
            escape_stagnation_rounds=escape_stagnation_rounds,
        )
        anchor_values = dict(next_seed_values)
        anchor_score = next_anchor_score
        anchor_source = next_seed_source
        autotune_event = _maybe_autotune_round_strategy(
            config_path,
            active_cfg,
            round_summaries,
            best_round,
            current_round_index=round_no,
        )
        if autotune_event is not None:
            round_entry["strategy_autotune"] = autotune_event
            strategy_autotune_events.append(autotune_event)
        if float(result["best_score"]) <= target_score:
            print(
                f"Plain optimize rounds: stop early at round {round_no} because target_score was reached"
            )
            break

    payload = {
        "mode": "plain-optimize-rounds",
        "config": str(config_path),
        "camera": camera_name,
        "round_count_requested": round_count,
        "round_count_completed": len(round_summaries),
        "rounds_output_dir": str(rounds_root),
        "best_round": best_round,
        "strategy_autotune_events": strategy_autotune_events,
        "rounds": round_summaries,
    }
    payload["summary_json"] = str(_write_rounds_summary(rounds_root, payload))
    return payload
def _run_explore_then_refine_rounds(
    config_path: Path,
    cfg: dict,
    base_output_dir: Path,
    camera_name: str,
    round_count: int,
    start_count: int,
    jitter_steps: float,
    seed: int,
    explore_max_iters: int,
    refine_max_iters: Optional[int],
    project_root: Optional[Path] = None,
) -> dict:
    if round_count <= 0:
        raise ValueError("round_count must be positive")

    _refine_max_iters = refine_max_iters if refine_max_iters is not None else int(cfg.get("max_iters", 0))
    overall_total_iters = round_count * (start_count * explore_max_iters + _refine_max_iters)
    rounds_root = _build_isolated_output_dir("rounds", camera_parent=camera_name, project_root=project_root)
    active_cfg = copy.deepcopy(cfg)
    target_score = float(cfg.get("target_score", 5.0))
    round_seed_policy = _resolve_round_seed_policy(cfg)
    anchor_values, anchor_score, anchor_source = _resolve_round_seed_anchor(
        config_path,
        camera_name,
        cfg,
        round_seed_policy,
    )
    if round_seed_policy["enabled"] or round_seed_policy.get("prefer_history_best", True):
        active_cfg = _cfg_with_initial_values(active_cfg, anchor_values)
        verify_params = active_cfg.get("parameters", {})
        anchor_mismatches = []
        for name, expected in anchor_values.items():
            actual = float(verify_params.get(name, {}).get("initial", float("nan")))
            if abs(actual - expected) > 1e-9:
                anchor_mismatches.append(f"{name}: expected={expected}, actual={actual}")
        if anchor_mismatches:
            print(
                f"ANCHOR APPLY MISMATCH: camera={camera_name}, source={anchor_source}, "
                f"score={anchor_score}, mismatches={anchor_mismatches}"
            )
        else:
            print(
                f"Anchor applied: camera={camera_name}, source={anchor_source}, "
                f"score={anchor_score}, values={_format_scalar_value_map(anchor_values)}, verify=OK"
            )
    round_summaries: List[dict] = []
    best_round: Optional[dict] = None
    escape_stagnation_rounds = 0
    escape_tolerance = float(round_seed_policy.get("score_tolerance", 1e-6))
    strategy_autotune_events: List[dict] = []
    timeout_fuse_limit = max(0, int(cfg.get("timeout_fuse_consecutive_round_failures", 2)))
    consecutive_timeout_like_failures = 0
    abort_reason: Optional[str] = None

    for round_index in range(round_count):
        round_no = round_index + 1
        round_output_dir = rounds_root / f"round_{round_no:02d}"
        round_seed = int(seed) + round_index
        print(
            f"Explore-then-refine rounds: round={round_no}/{round_count} "
            f"seed={round_seed} output_dir={round_output_dir}"
        )
        try:
            summary = _run_explore_then_refine_campaign(
                config_path=config_path,
                cfg=active_cfg,
                base_output_dir=base_output_dir,
                camera_name=camera_name,
                start_count=start_count,
                jitter_steps=jitter_steps,
                seed=round_seed,
                explore_max_iters=explore_max_iters,
                refine_max_iters=refine_max_iters,
                previous_escape_stagnation_rounds=escape_stagnation_rounds,
                anchor_score=anchor_score,
                output_root_dir=round_output_dir,
                round_index=round_index + 1,
                round_count=round_count,
                overall_total_iters=overall_total_iters,
                project_root=project_root,
            )
        except Exception as exc:
            timeout_like_failure = _is_timeout_like_round_failure(exc)
            consecutive_timeout_like_failures = (
                consecutive_timeout_like_failures + 1 if timeout_like_failure else 0
            )
            round_entry = {
                "round_index": round_no,
                "seed": round_seed,
                "campaign_output_dir": str(round_output_dir),
                "status": "failed",
                "error": str(exc),
                "timeout_like_failure": timeout_like_failure,
                "consecutive_timeout_like_failures": consecutive_timeout_like_failures,
            }
            round_summaries.append(round_entry)
            print(f"Explore-then-refine round {round_no} failed: {exc}")
            if _is_fatal_initial_board_error(exc):
                raise
            if timeout_fuse_limit > 0 and consecutive_timeout_like_failures >= timeout_fuse_limit:
                abort_reason = (
                    "Explore-then-refine timeout fuse tripped after "
                    f"{consecutive_timeout_like_failures} consecutive timeout-like failed rounds. "
                    f"Last error: {exc}"
                )
                round_entry["status"] = "aborted"
                round_entry["abort_reason"] = abort_reason
                print(abort_reason)
                break
            continue
        consecutive_timeout_like_failures = 0
        best_run = dict(summary["best_run"])
        strategy_payload = _load_strategy_adaptation_from_result_json(best_run.get("result_json"))
        current_param_order: List[str] = []
        if isinstance(strategy_payload, dict):
            raw_order = strategy_payload.get("current_param_order")
            if isinstance(raw_order, list):
                current_param_order = [str(item) for item in raw_order]
        round_entry = {
            "round_index": round_no,
            "seed": round_seed,
            "campaign_output_dir": summary["campaign_output_dir"],
            "status": "finished",
            "best_run": best_run,
            "current_param_order": current_param_order,
        }
        round_summaries.append(round_entry)
        if best_round is None or float(best_run["best_score"]) < float(best_round["best_run"]["best_score"]):
            best_round = round_entry
        escape_stagnation_rounds = _next_escape_stagnation_rounds(
            float(best_run["best_score"]),
            anchor_score,
            escape_tolerance,
            escape_stagnation_rounds,
        )
        next_seed_values, next_anchor_score, next_seed_source = _choose_next_round_seed_values(
            float(best_run["best_score"]),
            dict(best_run["best_values"]),
            policy=round_seed_policy,
            anchor_values=anchor_values,
            anchor_score=anchor_score,
            anchor_source=anchor_source,
        )
        active_cfg = _cfg_with_round_guidance(
            active_cfg,
            next_seed_values,
            strategy_payload,
            result_json=best_run.get("result_json"),
            escape_stagnation_rounds=escape_stagnation_rounds,
        )
        anchor_values = dict(next_seed_values)
        anchor_score = next_anchor_score
        anchor_source = next_seed_source
        autotune_event = _maybe_autotune_round_strategy(
            config_path,
            active_cfg,
            round_summaries,
            best_round,
            current_round_index=round_no,
        )
        if autotune_event is not None:
            round_entry["strategy_autotune"] = autotune_event
            strategy_autotune_events.append(autotune_event)
        if float(best_run["best_score"]) <= target_score:
            print(
                f"Explore-then-refine rounds: stop early at round {round_no} because target_score was reached"
            )
            break

    if best_round is not None:
        _write_best_values_to_vehicle_config(
            config_path,
            active_cfg,
            camera_name,
            float(best_round["best_run"]["best_score"]),
            best_round["best_run"]["best_values"],
            project_root=project_root,
        )

    payload = {
        "mode": "explore-then-refine-rounds",
        "config": str(config_path),
        "camera": camera_name,
        "round_count_requested": round_count,
        "round_count_completed": len(round_summaries),
        "rounds_output_dir": str(rounds_root),
        "best_round": best_round,
        "strategy_autotune_events": strategy_autotune_events,
        "rounds": round_summaries,
    }
    payload["summary_json"] = str(_write_rounds_summary(rounds_root, payload))
    if abort_reason is not None:
        raise RuntimeError(f"{abort_reason} See {payload['summary_json']}")
    if best_round is None:
        raise RuntimeError(f"All explore-then-refine rounds failed. See {payload['summary_json']}")
    return payload

