"""ScoringMixin — board scoring, aggregate score calculation, and scoring helpers."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.calibration.calib_types import BoardProfile, BoardScoreDetail, DetectionResult, TotalScoreDetail
from src.calibration.utils import _is_custom_marker_board_type


class ScoringMixin:

    @staticmethod
    def _is_visible(detection: DetectionResult, min_points: int) -> bool:
        return bool(detection.success and detection.point_count >= min_points)

    @staticmethod
    def _effective_detection_min_points(board: BoardProfile, detection: DetectionResult) -> int:
        min_points = max(1, int(board.min_detected_points))
        if detection.success and detection.detector == "template_match":
            if board.board_type == "checkerboard" or _is_custom_marker_board_type(board.board_type):
                return min(min_points, 9)
        return min_points

    @classmethod
    def _effective_scoring_min_points(
        cls,
        board: BoardProfile,
        real_detection: DetectionResult,
        sim_detection: DetectionResult,
    ) -> int:
        return min(
            cls._effective_detection_min_points(board, real_detection),
            cls._effective_detection_min_points(board, sim_detection),
        )

    def _snapshot_values(self) -> Dict[str, float]:
        return {p.name: p.value for p in self.params}

    def _score_board(
        self,
        board: BoardProfile,
        real_detection: DetectionResult,
        sim_detection: DetectionResult,
        sim_eval_image: Optional[np.ndarray] = None,
    ) -> BoardScoreDetail:
        real_min_points = self._effective_detection_min_points(board, real_detection)
        sim_min_points = self._effective_detection_min_points(board, sim_detection)
        scoring_min_points = self._effective_scoring_min_points(
            board,
            real_detection,
            sim_detection,
        )
        real_visible = self._is_visible(real_detection, real_min_points)

        if self.compare_only_if_reference_visible and not real_visible:
            return BoardScoreDetail(
                board_id=board.board_id,
                board_type=board.board_type,
                success=True,
                compared=False,
                reference_visible=False,
                sim_visible=self._is_visible(sim_detection, sim_min_points),
                total_score=0.0,
                rmse=0.0,
                mean_error=0.0,
                max_error=0.0,
                miss_rate=0.0,
                matched_point_count=0,
                failed_reason="not visible in reference, skipped",
            )

        if not sim_detection.success:
            penalty = board.fail_penalty
            if board.custom_detector == "template_match" and sim_detection.match_score is not None:
                penalty = board.fail_penalty * (1.0 - max(0.0, float(sim_detection.match_score)))
            return BoardScoreDetail(
                board_id=board.board_id,
                board_type=board.board_type,
                success=False,
                compared=True,
                reference_visible=real_visible,
                sim_visible=False,
                total_score=penalty,
                rmse=board.fail_penalty,
                mean_error=board.fail_penalty,
                max_error=board.fail_penalty,
                miss_rate=1.0,
                matched_point_count=0,
                failed_reason=sim_detection.error_message,
            )

        if not real_detection.success:
            return BoardScoreDetail(
                board_id=board.board_id,
                board_type=board.board_type,
                success=False,
                compared=True,
                reference_visible=False,
                sim_visible=self._is_visible(sim_detection, sim_min_points),
                total_score=board.fail_penalty,
                rmse=board.fail_penalty,
                mean_error=board.fail_penalty,
                max_error=board.fail_penalty,
                miss_rate=1.0,
                matched_point_count=0,
                failed_reason="reference board unavailable",
            )

        matched_points = min(real_detection.point_count, sim_detection.point_count)
        if matched_points < scoring_min_points:
            miss_rate = 1.0 - (matched_points / max(1, real_detection.point_count))
            return BoardScoreDetail(
                board_id=board.board_id,
                board_type=board.board_type,
                success=False,
                compared=True,
                reference_visible=real_visible,
                sim_visible=self._is_visible(sim_detection, sim_min_points),
                total_score=board.fail_penalty,
                rmse=board.fail_penalty,
                mean_error=board.fail_penalty,
                max_error=board.fail_penalty,
                miss_rate=miss_rate,
                matched_point_count=matched_points,
                failed_reason="insufficient detected points",
            )

        real_points = real_detection.ordered_points[:matched_points]
        sim_points = sim_detection.ordered_points[:matched_points]
        if board.board_type == "checkerboard" and board.board_size is not None:
            sim_points = self._align_checkerboard_ordering(
                sim_points, real_points, board.board_size
            )
        deltas = sim_points - real_points
        distances = np.linalg.norm(deltas, axis=1)
        rmse = float(np.sqrt(np.mean(np.square(distances))))
        mean_error = float(np.mean(distances))
        max_error = float(np.max(distances))
        if _is_custom_marker_board_type(board.board_type):
            geometric_penalty = self._custom_board_geometric_penalty(
                board,
                real_detection,
                sim_detection,
                sim_eval_image,
            )
            # Additive geometric penalty: rewards structure alignment after
            # homography warping. Unlike old max(image_penalty, rmse) which
            # conflated pixel content with geometry, this correctly correlates
            # with overlay quality: good alignment -> low penalty -> low score.
            rmse = rmse + geometric_penalty
            mean_error = mean_error + geometric_penalty
            max_error = max(max_error, mean_error + geometric_penalty)
        miss_rate = 1.0 - (matched_points / max(1, real_detection.point_count))
        total_score = rmse + board.alpha * miss_rate + board.beta * max_error

        return BoardScoreDetail(
            board_id=board.board_id,
            board_type=board.board_type,
            success=True,
            compared=True,
            reference_visible=real_visible,
            sim_visible=True,
            total_score=total_score,
            rmse=rmse,
            mean_error=mean_error,
            max_error=max_error,
            miss_rate=miss_rate,
            matched_point_count=matched_points,
        )

    def _aggregate_scores(
        self,
        board_scores: List[BoardScoreDetail],
        baseline_metrics: Optional[Dict[str, Dict[str, float]]],
    ) -> TotalScoreDetail:
        total_score = 0.0
        raw_total_score = 0.0
        degrade_penalty = 0.0
        raw_degrade_penalty = 0.0
        degraded_boards: List[str] = []
        isolated_outlier_boards = self._isolated_outlier_board_ids(board_scores, baseline_metrics)
        isolated_outlier_board_set = set(isolated_outlier_boards)
        objective_focus_multipliers = self._objective_focus_multiplier_map(
            board_scores,
            isolated_outlier_board_set,
        )
        has_critical_degrade = False
        compared_board_count = 0
        board_map = {b.board_id: b for b in self.boards}

        for score in board_scores:
            board = board_map[score.board_id]
            if not score.compared:
                continue

            compared_board_count += 1
            effective_weight = board.weight * objective_focus_multipliers.get(score.board_id, 1.0)
            weighted = effective_weight * score.total_score
            raw_total_score += weighted
            if score.board_id not in isolated_outlier_board_set:
                total_score += weighted

            if baseline_metrics is None:
                continue

            baseline = baseline_metrics.get(score.board_id)
            if baseline is None:
                continue

            degraded = False
            if not score.success:
                degraded = True
                raw_degrade_penalty += 1.0
                if score.board_id not in isolated_outlier_board_set:
                    degrade_penalty += 1.0
            else:
                rmse_delta = score.rmse - float(baseline.get("rmse", score.rmse))
                max_delta = score.max_error - float(
                    baseline.get("max_error", score.max_error)
                )
                miss_delta = score.miss_rate - float(
                    baseline.get("miss_rate", score.miss_rate)
                )
                if rmse_delta > board.degrade_threshold_rmse:
                    degraded = True
                if max_delta > board.degrade_threshold_max_error:
                    degraded = True
                if miss_delta > board.degrade_threshold_miss_rate:
                    degraded = True
                if degraded:
                    degrade_delta = (
                        max(0.0, rmse_delta)
                        + max(0.0, max_delta)
                        + max(0.0, miss_delta)
                    )
                    raw_degrade_penalty += degrade_delta
                    if score.board_id not in isolated_outlier_board_set:
                        degrade_penalty += degrade_delta

            if degraded and score.board_id not in isolated_outlier_board_set:
                degraded_boards.append(score.board_id)
                if board.critical:
                    has_critical_degrade = True

        if compared_board_count == 0:
            return TotalScoreDetail(
                success=False,
                total_score=self.no_signal_penalty,
                raw_total_score=self.no_signal_penalty,
                degrade_penalty=0.0,
                has_critical_degrade=False,
                degraded_boards=[],
                isolated_outlier_boards=[],
                compared_board_count=0,
                board_scores=board_scores,
                failed_reason="no comparable boards in current frame",
            )

        raw_total_score += self.degrade_lambda * raw_degrade_penalty
        total_score += self.degrade_lambda * degrade_penalty

        return TotalScoreDetail(
            success=not has_critical_degrade,
            total_score=total_score,
            raw_total_score=raw_total_score,
            degrade_penalty=degrade_penalty,
            has_critical_degrade=has_critical_degrade,
            degraded_boards=degraded_boards,
            isolated_outlier_boards=isolated_outlier_boards,
            compared_board_count=compared_board_count,
            board_scores=board_scores,
            failed_reason="critical board degraded" if has_critical_degrade else None,
        )
