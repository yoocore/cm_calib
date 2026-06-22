"""OrchestrationMixin — history snapshots, trial logging, calibration summary, and progress reporting."""
import copy
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.calibration.calib_types import TotalScoreDetail
from src.calibration.utils import (
    _camera_name_from_output_dir,
    _format_scalar_value_map,
)


def _emit_cli_progress_json(payload: dict) -> None:
    """Emit progress JSON line for CLI consumers."""
    print("CALIBRATION_PROGRESS_JSON:", json.dumps(payload, ensure_ascii=False, sort_keys=True))


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
        }

    def _print_calibration_summary(self, summary: Dict[str, object]) -> None:
        print(
            "Calibration summary: "
            f"camera={summary['camera']} "
            f"start_score={float(summary['start_score']):.6f} "
            f"final_score={float(summary['final_score']):.6f} "
            f"improvement={float(summary['score_improvement']):.6f} "
            f"rounds={int(summary['iteration_round_count'])} "
            f"elapsed={summary['total_elapsed_text']} "
            f"stop_reason={summary['stop_reason']} "
            f"passed={summary['passed']}"
        )
        print(
            "Start values:",
            _format_scalar_value_map(dict(summary["start_values"])),
        )
        print(
            "Final values:",
            _format_scalar_value_map(dict(summary["final_values"])),
        )

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
        with open(self.output_dir / "result.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
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
        """Delete intermediate iter_*.png, keep only initial and final best."""
        keep_stems = {"initial", "initial_score", "initial_overlay"}
        best_stem = final_best_img.stem
        keep_stems.add(best_stem)
        keep_stems.add(f"{best_stem}_score")
        keep_stems.add(f"{best_stem}_overlay")
        for img_path in list(self.output_dir.glob("iter_*.png")):
            if img_path.stem not in keep_stems:
                try:
                    img_path.unlink()
                except OSError:
                    pass
