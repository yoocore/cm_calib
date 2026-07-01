import math
import numpy as np
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.calibration.calib_types import TotalScoreDetail
from src.calibration.sensitivity import build_geometric_sensitivity




class GaussNewtonAccumulator:
    """Per-point Gauss-Newton step from CD single-param trial data.

    Each CD iteration perturbs every parameter by ±δ, re-renders, and detects
    points.  This class captures the per-board detection positions at each
    perturbation to build a proper Jacobian via central differences.

    Key insight:  J ∈ ℝ^(2K × N) captures param coupling (e.g. yaw ↔ pos_x)
    that 1D coordinate descent cannot.  Since existing CD trial renders are
    reused, the GN step costs zero extra renderings.

    The GN solve uses Levenberg-Marquardt damping:
        (J^T J + λ·diag(J^T J))·Δ = -J^T r
    """

    def __init__(self):
        # {param_name: {board_id: ndarray (N, 2)}}  — positions at ±δ
        self._pos_plus: Dict[str, Dict[str, np.ndarray]] = {}
        self._pos_minus: Dict[str, Dict[str, np.ndarray]] = {}
        self._deltas: Dict[str, float] = {}
        self._param_names: List[str] = []

    def record_trial(self, param_name: str, direction: float, delta: float,
                     board_points: Dict[str, np.ndarray]) -> None:
        """Store per-board detection positions from a single-param trial."""
        if param_name not in self._param_names:
            self._param_names.append(param_name)
            self._pos_plus[param_name] = {}
            self._pos_minus[param_name] = {}
        store = self._pos_plus if direction > 0 else self._pos_minus
        store[param_name] = {bid: pts.copy() for bid, pts in board_points.items()}
        self._deltas[param_name] = abs(delta)

    def compute_gn_step(
        self,
        params: List,
        best_values: Dict[str, float],
        best_sim_positions: Dict[str, np.ndarray],
        real_detections: Dict,
        damping: float = 1.0,
    ) -> Optional[Dict[str, float]]:
        """Compute LM-regularised GN step from accumulated trial data.

        The step Δ solves  min_Δ  ||J·Δ + r||²  with LM damping.
        """
        # Only params with both +δ and -δ recorded
        valid = [n for n in self._param_names
                 if n in self._pos_plus and n in self._pos_minus]
        if not valid:
            return None

        # Build residual r = best_sim - real, point by point
        r_vals: List[float] = []
        point_keys: List[tuple] = []  # (board_id, point_idx) per 2D row pair
        for bid in sorted(best_sim_positions.keys() & set(real_detections.keys())):
            sim = best_sim_positions[bid]
            real = real_detections[bid].ordered_points
            n = min(len(sim), len(real))
            if n < 4:
                continue
            for i in range(n):
                r_vals.append(float(sim[i, 0] - real[i, 0]))
                r_vals.append(float(sim[i, 1] - real[i, 1]))
                point_keys.append((bid, i))

        K = len(point_keys)
        if K < 4:
            return None

        r = np.array(r_vals, dtype=np.float64)
        m = len(valid)
        J = np.zeros((2 * K, m), dtype=np.float64)

        # Fill Jacobian via central differences
        for j, pname in enumerate(valid):
            pp = self._pos_plus[pname]
            pm = self._pos_minus[pname]
            delta_p = max(abs(self._deltas.get(pname, 1.0)), 1e-12)
            denom = 2.0 * delta_p
            for row, (bid, i) in enumerate(point_keys):
                pts_p = pp.get(bid)
                pts_m = pm.get(bid)
                if pts_p is None or pts_m is None or i >= min(len(pts_p), len(pts_m)):
                    continue
                J[2 * row,     j] = (pts_p[i, 0] - pts_m[i, 0]) / denom
                J[2 * row + 1, j] = (pts_p[i, 1] - pts_m[i, 1]) / denom

        # LM: (J^T J + λ·diag(J^T J))·Δ = -J^T r
        JTJ = J.T @ J
        JTr = J.T @ r
        diag_JTJ = np.diag(np.abs(JTJ))
        lambda_eff = max(damping, 1e-8)
        try:
            delta_x = np.linalg.solve(JTJ + lambda_eff * np.diag(diag_JTJ), -JTr)
        except np.linalg.LinAlgError:
            return None

        # Step magnitude clamp (prevent divergence on noisy Jacobian)
        step_norm = np.linalg.norm(delta_x)
        max_step = 5.0
        if step_norm > max_step:
            delta_x = delta_x * (max_step / step_norm)

        result: Dict[str, float] = {}
        for j, pname in enumerate(valid):
            p = next((pp for pp in params if pp.name == pname), None)
            if p is None:
                continue
            new_val = best_values[pname] + delta_x[j]
            new_val = max(p.min_value, min(p.max_value, new_val))
            result[pname] = new_val
        return result if result else None

class CoordinateDescentMixin:
    """Coordinate Descent optimizer methods for CameraCalibrator."""

    @dataclass
    class TrialResult:
        total_detail: Optional[TotalScoreDetail] = None
        img_path: Optional[Path] = None
        score: float = float("inf")
        accepted: bool = False
        accepted_reason: str = ""
        joint_candidate_reason: Optional[str] = None
        recovered: bool = False
        failed_reason: Optional[str] = None

    def _trial_multipliers_for_param(self, param_name: str) -> List[float]:
        if self.strategy_adaptation_enabled:
            active_profile = self._strategy_active_profile()
            profile_single = [
                float(value) for value in active_profile.get("single_trial_multipliers", [1.0])
            ]
            if not self._is_joint_exploration_param(param_name):
                return self._merge_trial_multiplier_sequences(profile_single)
            profile_joint = [
                float(value) for value in active_profile.get("joint_trial_multipliers", [])
            ]
            return self._merge_trial_multiplier_sequences(
                profile_single,
                self.joint_exploration_trial_multipliers,
                profile_joint,
            )
        if not self._is_joint_exploration_param(param_name):
            return [1.0]
        return self.joint_exploration_trial_multipliers

    def _joint_exploration_candidate_reason(
        self,
        param_name: str,
        baseline_score: float,
        candidate_detail: TotalScoreDetail,
        candidate_score: float,
    ) -> Optional[str]:
        if not self._is_joint_exploration_param(param_name):
            return None
        if candidate_detail.compared_board_count <= 0 or candidate_detail.has_critical_degrade:
            return None
        score_worsen = candidate_score - baseline_score
        if score_worsen > self.joint_exploration_max_single_worsen:
            return None
        return f"joint_exploration_candidate[{score_worsen:.3f}]"

    def _candidate_move_sort_key(self, move: Dict[str, object]) -> Tuple[int, int, float]:
        name = str(move["name"])
        order_index = self.param_order_index.get(name, len(self.param_order_index))
        if self.strategy_adaptation_enabled and self.strategy_reorder_params:
            order_map = {
                param.name: index
                for index, param in enumerate(self._ordered_params_for_iteration())
            }
            order_index = order_map.get(name, order_index)
        return (
            0 if self._is_joint_exploration_param(name) else 1,
            order_index,
            float(move["score"]),
        )

    def _optimize_coordinate_descent(self) -> dict:
        return self._optimize_coordinate_descent_impl()

    def _run_single_param_trial(
        self,
        p,
        trial_value: float,
        base_values: Dict[str, float],
        base_score: float,
        best_total_detail: TotalScoreDetail,
        best_baseline: Dict,
        it: int,
        direction: float,
        trial_multiplier: float,
        iteration_strategy_stats: Dict,
        history: List[dict],
        best_score: float,
        best_values: Dict[str, float],
        best_img: Path,
    ) -> "CoordinateDescentMixin.TrialResult":
        result = self.TrialResult()
        self._total_trial_count += 1
        tag = f"iter_{it:04d}_{p.name}_{'p' if direction > 0 else 'n'}"
        try:
            self._apply_value_map({p.name: trial_value})
            total_detail, img_path = self.evaluate(tag, baseline_metrics=best_baseline)
            score = total_detail.total_score
            accepted, accepted_reason = self._acceptance_decision(
                baseline_score=base_score,
                baseline_detail=best_total_detail,
                candidate_score=score,
                candidate_detail=total_detail,
            )
            joint_candidate_reason = None
            if not accepted:
                joint_candidate_reason = self._joint_exploration_candidate_reason(
                    p.name, base_score, total_detail, score
                )
            self._record_strategy_trial(
                iteration_strategy_stats,
                param_name=p.name,
                accepted=accepted,
                joint_candidate=joint_candidate_reason is not None,
                score_delta=score - base_score,
                trial_multiplier=trial_multiplier,
                baseline_detail=best_total_detail,
                candidate_detail=total_detail,
            )
            history.append(
                self._make_history_entry(
                    it, total_detail, img_path, accepted,
                    meta={
                        "phase": "single",
                        "param": p.name,
                        "trial": trial_value,
                        "direction": "+" if direction > 0 else "-",
                        "trial_multiplier": trial_multiplier,
                        "accepted_reason": accepted_reason,
                        "joint_candidate_reason": joint_candidate_reason,
                    },
                )
            )
            print(
                f"iter={it} phase=single param={p.name} trial={trial_value:.{p.decimals}f} "
                f"total_score={score:.6f} compared={total_detail.compared_board_count} "
                f"degrade={total_detail.degrade_penalty:.6f} "
                f"critical_degrade={total_detail.has_critical_degrade} "
                f"accepted={accepted} accepted_reason={accepted_reason} "
                f"joint_candidate={joint_candidate_reason or '-'} "
                f"direction={'+' if direction > 0 else '-'} "
                f"step_scale={trial_multiplier:g} "
                f"{self._top_board_summary(total_detail)}"
            )
            self._flush_progress_if_needed(
                best_score=best_score, best_values=best_values,
                best_total_detail=best_total_detail, best_img=best_img,
                stop_reason="running", history=history,
            )
            if accepted and score + self.min_improve < best_score:
                latest_best_values = dict(best_values)
                latest_best_values[p.name] = trial_value
                self._write_progress_result(
                    best_score=score,
                    best_values=latest_best_values,
                    best_total_detail=total_detail, best_img=img_path,
                    stop_reason="running",
                    history=self._trim_history(history),
                    in_progress=True,
                )
            self._apply_value_map({p.name: base_values[p.name]})

            result.total_detail = total_detail
            result.img_path = img_path
            result.score = score
            result.accepted = accepted
            result.accepted_reason = accepted_reason
            result.joint_candidate_reason = joint_candidate_reason
            self._append_trial_log(
                iteration=it,
                score=score,
                accepted=accepted,
                phase="single",
                param_name=p.name,
                direction="+" if direction > 0 else "-",
                trial_multiplier=trial_multiplier,
                accepted_reason=accepted_reason,
            )
        except RuntimeError as exc:
            if "RENDERING_BROKEN" in str(exc):
                raise  # Propagate rendering failure — abort calibration
            restored = self._recover_after_runtime_error(base_values, exc)
            history.append(
                self._make_history_entry(
                    it, best_total_detail, best_img, False,
                    failed_reason=str(exc),
                    meta={
                        "phase": "single_runtime_error",
                        "param": p.name,
                        "trial": trial_value,
                        "direction": "+" if direction > 0 else "-",
                        "trial_multiplier": trial_multiplier,
                        "recovered": restored,
                    },
                )
            )
            print(
                f"iter={it} phase=single param={p.name} trial={trial_value:.{p.decimals}f} "
                f"runtime_error={exc} recovered={restored}"
            )
            self._flush_progress_if_needed(
                best_score=best_score, best_values=best_values,
                best_total_detail=best_total_detail, best_img=best_img,
                stop_reason="running", history=history,
            )
            if not restored:
                raise RuntimeError(f"Failed to recover after Script Control runtime error: {exc}")
            self._append_trial_log(
                iteration=it,
                score=float("inf"),
                accepted=False,
                phase="single_runtime_error",
                param_name=p.name,
                direction="+" if direction > 0 else "-",
                trial_multiplier=trial_multiplier,
                failed_reason=str(exc),
                recovered=restored,
            )
            result.recovered = True
            result.failed_reason = str(exc)
        return result

    def _run_joint_param_trial(
        self,
        name: str,
        trial_value: float,
        previous_value: float,
        joint_score: float,
        joint_total_detail: TotalScoreDetail,
        joint_baseline: Dict,
        it: int,
        move: Dict[str, object],
        iteration_strategy_stats: Dict,
        history: List[dict],
        best_score: float,
        best_values: Dict[str, float],
        best_total_detail: TotalScoreDetail,
        best_img: Path,
    ) -> "CoordinateDescentMixin.TrialResult":
        result = self.TrialResult()
        self._total_trial_count += 1
        try:
            self._apply_value_map({name: trial_value})
            total_detail, img_path = self.evaluate(
                f"iter_{it:04d}_joint_{name}",
                baseline_metrics=joint_baseline,
            )
            score = total_detail.total_score
            accepted, accepted_reason = self._acceptance_decision(
                baseline_score=joint_score,
                baseline_detail=joint_total_detail,
                candidate_score=score,
                candidate_detail=total_detail,
            )
            self._record_strategy_trial(
                iteration_strategy_stats,
                param_name=name,
                accepted=accepted,
                joint_candidate=False,
                score_delta=score - joint_score,
                trial_multiplier=float(move.get("trial_multiplier", 1.0)),
                baseline_detail=joint_total_detail,
                candidate_detail=total_detail,
            )
            history.append(
                self._make_history_entry(
                    it, total_detail, img_path, accepted,
                    meta={
                        "phase": "joint",
                        "param": name,
                        "trial": trial_value,
                        "direction": "+" if float(move["direction"]) > 0 else "-",
                        "joint_params": move.get("joint_params", []),
                        "accepted_reason": accepted_reason,
                    },
                )
            )
            print(
                f"iter={it} phase=joint param={name} trial={trial_value:.4f} "
                f"total_score={score:.6f} compared={total_detail.compared_board_count} "
                f"degrade={total_detail.degrade_penalty:.6f} "
                f"critical_degrade={total_detail.has_critical_degrade} accepted={accepted} "
                f"accepted_reason={accepted_reason} "
                f"{self._top_board_summary(total_detail)}"
            )
            self._flush_progress_if_needed(
                best_score=best_score, best_values=best_values,
                best_total_detail=best_total_detail, best_img=best_img,
                stop_reason="running", history=history,
            )

            result.total_detail = total_detail
            result.img_path = img_path
            result.score = score
            result.accepted = accepted
            result.accepted_reason = accepted_reason
            self._append_trial_log(
                iteration=it,
                score=score,
                accepted=accepted,
                phase="joint",
                param_name=name,
                direction="+" if float(move["direction"]) > 0 else "-",
                trial_multiplier=float(move.get("trial_multiplier", 1.0)),
                accepted_reason=accepted_reason,
            )
        except RuntimeError as exc:
            if "RENDERING_BROKEN" in str(exc):
                raise  # Propagate rendering failure — abort calibration
            restored = self._recover_after_runtime_error({name: previous_value}, exc)
            history.append(
                self._make_history_entry(
                    it, joint_total_detail, best_img, False,
                    failed_reason=str(exc),
                    meta={
                        "phase": "joint_runtime_error",
                        "param": name,
                        "trial": trial_value,
                        "direction": "+" if float(move["direction"]) > 0 else "-",
                        "joint_params": move.get("joint_params", []),
                        "recovered": restored,
                    },
                )
            )
            print(
                f"iter={it} phase=joint param={name} trial={trial_value:.4f} "
                f"runtime_error={exc} recovered={restored}"
            )
            self._flush_progress_if_needed(
                best_score=best_score, best_values=best_values,
                best_total_detail=best_total_detail, best_img=best_img,
                stop_reason="running", history=history,
            )
            if not restored:
                raise RuntimeError(f"Failed to recover after Script Control runtime error: {exc}")
            self._append_trial_log(
                iteration=it,
                score=float("inf"),
                accepted=False,
                phase="joint_runtime_error",
                param_name=name,
                direction="+" if float(move["direction"]) > 0 else "-",
                trial_multiplier=float(move.get("trial_multiplier", 1.0)),
                failed_reason=str(exc),
                recovered=restored,
            )
            result.recovered = True
            result.failed_reason = str(exc)
        return result

    def _optimize_coordinate_descent_impl(self, max_iters: Optional[int] = None) -> dict:
        limit = max_iters if max_iters is not None else self.max_iters
        self._ensure_live_log()
        self._historical_best_snapshot = None
        if not hasattr(self, '_pref_dir_stagnation'):
            self._pref_dir_stagnation = {}
        if not hasattr(self, '_param_freeze_count'):
            self._param_freeze_count = {}
        if not hasattr(self, '_frozen_params'):
            self._frozen_params = set()
        if not hasattr(self, '_freeze_timers'):
            self._freeze_timers = {}
        if getattr(self, 'use_gauss_newton', False):
            self._gn_acc = GaussNewtonAccumulator()
        if self.real_detections is None:
            self.real_detections = self._detect_reference_boards()

        self._print_run_summary()
        self._preflight_capture_aspect_ratio()
        self.preflight_script_control()
        try:
            self._apply_initial_value_map_with_retry(
                self._snapshot_values(),
                "Failed initial Script Control apply",
            )
        except RuntimeError as exc:
            print(f"Initial Script Control apply skipped: {exc}")
        best_total_detail, best_img = self.evaluate("initial", baseline_metrics=None)
        self._raise_if_initial_board_failures(best_total_detail)
        best_score = best_total_detail.total_score
        best_baseline = self._as_baseline_metrics(best_total_detail)
        best_values = {p.name: p.value for p in self.params}
        if getattr(self, 'use_gauss_newton', False):
            self._best_sim_detections = {
                bid: det.ordered_points.copy()
                for bid, det in self._last_sim_detections.items()
            }
        self._remember_historical_best_snapshot(
            score=best_score,
            values=best_values,
            total_detail=best_total_detail,
            img_path=best_img,
        )
        stop_reason = "max_iters_reached"
        initial_score_image = self._build_score_image_for_snapshot(
            best_img,
            best_total_detail,
            best_values,
            output_path=best_img.with_name("initial_score.png"),
        )

        history = [
            {
                "iter": 0,
                "total_score": best_score,
                "compared_board_count": best_total_detail.compared_board_count,
                "degrade_penalty": best_total_detail.degrade_penalty,
                "has_critical_degrade": best_total_detail.has_critical_degrade,
                "degraded_boards": best_total_detail.degraded_boards,
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
                        "geometric_penalty": s.geometric_penalty,
                        "failed_reason": s.failed_reason,
                    }
                    for s in best_total_detail.board_scores
                ],
                "accepted": True,
                "image": str(best_img),
                "score_image": str(initial_score_image) if initial_score_image else None,
                "values": best_values.copy(),
            }
        ]

        print(
            "iter=0 "
            f"total_score={best_score:.6f} "
            f"compared_boards={best_total_detail.compared_board_count} "
            f"degrade_penalty={best_total_detail.degrade_penalty:.6f} "
            f"{self._top_board_summary(best_total_detail)}"
        )
        self._write_progress_result(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason="running",
            history=self._trim_history(history),
            in_progress=True,
        )

        it = 1
        consecutive_no_improve = 0
        while it <= limit:
            improved_in_iter = False
            self._total_iteration_count += 1
            base_values = self._snapshot_values()
            base_score = best_score
            if getattr(self, 'use_gauss_newton', False):
                self._gn_acc = GaussNewtonAccumulator()
            candidate_moves: List[Dict[str, object]] = []
            ordered_params = self._ordered_params_for_iteration()
            iteration_strategy_stats = self._new_strategy_iteration_stats()
            iteration_meta: Dict[str, object] = {
                "phase": "iteration_start",
            }
            strategy_meta = self._strategy_iteration_meta(ordered_params)
            if strategy_meta is not None:
                iteration_meta["strategy"] = strategy_meta
            history.append(
                self._make_history_entry(
                    it,
                    best_total_detail,
                    best_img,
                    True,
                    meta=iteration_meta,
                )
            )
            self._flush_progress_if_needed(
                best_score=best_score,
                best_values=best_values,
                best_total_detail=best_total_detail,
                best_img=best_img,
                stop_reason="running",
                history=history,
            )

            if not hasattr(self, '_geometric_sensitivity'):
                self._geometric_sensitivity = build_geometric_sensitivity(
                    self.boards, self.params, self.real_img.shape[:2]
                )

            for p in ordered_params:
                if p.name in self._frozen_params:
                    continue
                preferred_direction = self.preferred_directions.get(p.name, 1.0)
                if self._pref_dir_stagnation.get(p.name, 0) >= 3:
                    preferred_direction = -preferred_direction
                    self.preferred_directions[p.name] = preferred_direction
                    self._pref_dir_stagnation[p.name] = 0
                trial_directions: List[float] = [preferred_direction, -preferred_direction]
                best_param_move: Optional[Dict[str, object]] = None
                seen_trial_values: set[float] = set()
                stop_param_search = False
                any_accepted = False
                effective_step = self._strategy_effective_step(p)

                if getattr(self, "jitter_eps", 0.0) > 0:
                    self.jitter_eps = max(0.0, self.jitter_eps * getattr(self, "jitter_decay", 0.98))
                    jitter = random.gauss(0, self.jitter_eps * effective_step)
                    effective_step = max(p.min_step, effective_step + jitter)

                for direction in trial_directions:
                    for trial_multiplier in self._trial_multipliers_for_param(p.name):
                        trial_value = self._quantize_param_value(
                            p,
                            base_values[p.name] + direction * effective_step * trial_multiplier,
                        )
                        if math.isclose(
                            trial_value,
                            base_values[p.name],
                            rel_tol=0.0,
                            abs_tol=1e-12,
                        ):
                            continue
                        if any(
                            math.isclose(trial_value, seen, rel_tol=0.0, abs_tol=1e-12)
                            for seen in seen_trial_values
                        ):
                            continue
                        seen_trial_values.add(trial_value)

                        trial_result = self._run_single_param_trial(
                            p=p,
                            trial_value=trial_value,
                            base_values=base_values,
                            base_score=base_score,
                            best_total_detail=best_total_detail,
                            best_baseline=best_baseline,
                            it=it,
                            direction=direction,
                            trial_multiplier=trial_multiplier,
                            iteration_strategy_stats=iteration_strategy_stats,
                            history=history,
                            best_score=best_score,
                            best_values=best_values,
                            best_img=best_img,
                        )

                        if trial_result.recovered:
                            it += 1
                            if it > limit:
                                stop_param_search = True
                                break
                            continue

                        total_detail = trial_result.total_detail
                        img_path = trial_result.img_path
                        score = trial_result.score
                        accepted = trial_result.accepted
                        accepted_reason = trial_result.accepted_reason
                        joint_candidate_reason = trial_result.joint_candidate_reason

                        if getattr(self, 'use_gauss_newton', False):
                            sim_dets = getattr(self, '_last_sim_detections', {})
                            board_points = {
                                bid: det.ordered_points
                                for bid, det in sim_dets.items()
                            }
                            delta = abs(trial_value - base_values[p.name])
                            self._gn_acc.record_trial(
                                p.name, direction, delta, board_points,
                            )

                        eligible_for_joint = accepted or joint_candidate_reason is not None
                        if eligible_for_joint and (
                            best_param_move is None
                            or (
                                accepted
                                and not bool(best_param_move.get("accepted", False))
                            )
                            or score < float(best_param_move["score"])
                        ):
                            best_param_move = {
                                "name": p.name,
                                "value": trial_value,
                                "direction": direction,
                                "score": score,
                                "trial_multiplier": trial_multiplier,
                                "total_detail": total_detail,
                                "img_path": img_path,
                                "baseline": self._as_baseline_metrics(total_detail),
                                "accepted": accepted,
                                "joint_candidate_reason": joint_candidate_reason,
                            }

                        if accepted:
                            self.preferred_directions[p.name] = direction
                            any_accepted = True

                        it += 1
                        if it > limit:
                            stop_param_search = True
                            break

                    if any_accepted and self.stop_after_first_accepted_direction and not self._is_joint_exploration_param(p.name):
                        stop_param_search = True

                    if stop_param_search:
                        break

                if best_param_move is None:
                    p.step = max(p.min_step, p.step * self.step_decay)
                    self._pref_dir_stagnation[p.name] = self._pref_dir_stagnation.get(p.name, 0) + 1
                    if p.step <= p.min_step + 1e-12:
                        self._param_freeze_count[p.name] = self._param_freeze_count.get(p.name, 0) + 1
                        if self._param_freeze_count[p.name] >= 3:
                            self._frozen_params.add(p.name)
                    else:
                        self._param_freeze_count[p.name] = 0
                else:
                    candidate_moves.append(best_param_move)
                    self._pref_dir_stagnation[p.name] = 0
                    self._param_freeze_count[p.name] = 0

                if it > limit:
                    break

            accepted_params_in_pass: List[str] = []
            fallback_move: Optional[Dict[str, object]] = None
            accepted_candidate_moves = [
                move for move in candidate_moves if bool(move.get("accepted", False))
            ]
            if accepted_candidate_moves:
                fallback_move = min(
                    accepted_candidate_moves, key=lambda item: float(item["score"])
                )

            if candidate_moves and it <= limit:
                candidate_moves.sort(key=self._candidate_move_sort_key)
                joint_values = base_values.copy()
                joint_score = base_score
                joint_total_detail = best_total_detail
                joint_baseline = best_baseline
                joint_img = best_img

                self._apply_value_map_or_recover(
                    base_values,
                    "Failed to restore base values before joint phase",
                )

                for move in candidate_moves:
                    name = str(move["name"])
                    trial_value = float(move["value"])
                    previous_value = float(joint_values[name])
                    move_with_params = dict(move)
                    move_with_params["joint_params"] = accepted_params_in_pass + [name]

                    trial_result = self._run_joint_param_trial(
                        name=name,
                        trial_value=trial_value,
                        previous_value=previous_value,
                        joint_score=joint_score,
                        joint_total_detail=joint_total_detail,
                        joint_baseline=joint_baseline,
                        it=it,
                        move=move_with_params,
                        iteration_strategy_stats=iteration_strategy_stats,
                        history=history,
                        best_score=best_score,
                        best_values=best_values,
                        best_total_detail=best_total_detail,
                        best_img=best_img,
                    )

                    if trial_result.recovered:
                        it += 1
                        if it > limit:
                            break
                        continue

                    total_detail = trial_result.total_detail
                    img_path = trial_result.img_path
                    score = trial_result.score
                    accepted = trial_result.accepted

                    if accepted:
                        joint_values[name] = trial_value
                        joint_score = score
                        joint_total_detail = total_detail
                        joint_baseline = self._as_baseline_metrics(total_detail)
                        joint_img = img_path
                        accepted_params_in_pass.append(name)
                        improved_in_iter = True
                    else:
                        self._apply_value_map({name: previous_value})

                    it += 1
                    if it > limit:
                        break

                if accepted_params_in_pass:
                    best_score = joint_score
                    best_total_detail = joint_total_detail
                    best_baseline = joint_baseline
                    best_img = joint_img
                    best_values = joint_values.copy()
                    if getattr(self, 'use_gauss_newton', False):
                        self._best_sim_detections = {
                            bid: det.ordered_points.copy()
                            for bid, det in self._last_sim_detections.items()
                        }
                    self._remember_historical_best_snapshot(
                        score=best_score,
                        values=best_values,
                        total_detail=best_total_detail,
                        img_path=best_img,
                    )
                    self._apply_value_map_or_recover(
                        joint_values,
                        "Failed to apply joint values after accepted joint update",
                    )
                    self._write_progress_result(
                        best_score=best_score,
                        best_values=best_values,
                        best_total_detail=best_total_detail,
                        best_img=best_img,
                        stop_reason="running",
                        history=self._trim_history(history),
                        in_progress=True,
                    )
                else:
                    self._apply_value_map_or_recover(
                        base_values,
                        "Failed to restore base values after rejected joint phase",
                    )

            if fallback_move is not None and (
                not accepted_params_in_pass
                or float(fallback_move["score"]) + self.min_improve < best_score
            ):
                fallback_name = str(fallback_move["name"])
                fallback_value = float(fallback_move["value"])
                fallback_values = base_values.copy()
                fallback_values[fallback_name] = fallback_value
                self._apply_value_map_or_recover(
                    fallback_values,
                    f"Failed to apply fallback values for {fallback_name}",
                )
                best_score = float(fallback_move["score"])
                best_total_detail = fallback_move["total_detail"]  # type: ignore[assignment]
                best_baseline = fallback_move["baseline"]  # type: ignore[assignment]
                best_img = fallback_move["img_path"]  # type: ignore[assignment]
                best_values = fallback_values.copy()
                self._remember_historical_best_snapshot(
                    score=best_score,
                    values=best_values,
                    total_detail=best_total_detail,
                    img_path=best_img,
                )
                improved_in_iter = True
                print(
                    f"single_fallback accepted_param={fallback_name} "
                    f"best_score={best_score:.6f} "
                    f"{self._top_board_summary(best_total_detail)}"
                )
                self._write_progress_result(
                    best_score=best_score,
                    best_values=best_values,
                    best_total_detail=best_total_detail,
                    best_img=best_img,
                    stop_reason="running",
                    history=self._trim_history(history),
                    in_progress=True,
                )

            if accepted_params_in_pass:
                joined = ",".join(accepted_params_in_pass)
                print(
                    f"joint_update accepted_params={joined} best_score={best_score:.6f} "
                    f"{self._top_board_summary(best_total_detail)}"
                )
            # Gauss-Newton step (captures parameter coupling, e.g. yaw↔pos_x)
            if getattr(self, 'use_gauss_newton', False) and it >= 2:
                best_positions = getattr(self, '_best_sim_detections', None)
                if best_positions is not None and self.real_detections is not None:
                    gn = self._gn_acc.compute_gn_step(
                        self.params, best_values,
                        best_positions, self.real_detections,
                        damping=getattr(self, 'gn_damping', 1.0),
                    )
                    if gn:
                        self._apply_value_map_or_recover(
                            gn, "Failed to apply GN step",
                        )
                        gn_detail, gn_img = self.evaluate(f"gn_{it}", best_baseline)
                        gn_score = gn_detail.total_score
                        if gn_score + self.min_improve < best_score:
                            best_score = gn_score
                            best_total_detail = gn_detail
                            best_img = gn_img
                            best_values = gn.copy()
                            self._best_sim_detections = {
                                bid: det.ordered_points.copy()
                                for bid, det in self._last_sim_detections.items()
                            }
                            improved_in_iter = True
                            print(
                                f"gauss_newton accepted best_score={best_score:.6f} "
                                f"{self._top_board_summary(best_total_detail)}"
                            )
                        else:
                            self._apply_value_map_or_recover(
                                best_values, "Failed to restore after GN rejection",
                            )



            self._finalize_strategy_iteration(
                it,
                iteration_strategy_stats,
                improved_in_iter=improved_in_iter,
            )

            if best_score <= self.target_score:
                stop_reason = "target_score"
                print(f"Target score reached: {best_score:.6f} <= {self.target_score:.6f}.")
                break

            if not improved_in_iter and all(p.step <= p.min_step + 1e-12 for p in self.params):
                stop_reason = "all_steps_minimum"
                print("No further improvement and all steps at min_step. Stop.")
                break

            # Early stop: N consecutive iterations without any improvement
            if improved_in_iter:
                consecutive_no_improve = 0
            else:
                consecutive_no_improve += 1
            early_stop_patience = getattr(self, 'early_stop_patience', 30)
            if consecutive_no_improve >= early_stop_patience:
                stop_reason = "early_stop"
                print(f"No improvement for {consecutive_no_improve} iterations (patience={early_stop_patience}). Stop.")
                break

            # Freeze timeout: auto-unfreeze params frozen for too many iterations
            freeze_timeout = getattr(self, 'freeze_timeout', 15)
            for fp in list(self._frozen_params):
                self._freeze_timers[fp] = self._freeze_timers.get(fp, 0) + 1
                if self._freeze_timers[fp] >= freeze_timeout:
                    self._frozen_params.discard(fp)
                    self._freeze_timers[fp] = 0
                    self._param_freeze_count[fp] = 0
                    self._pref_dir_stagnation[fp] = 0
                    print(f"  freeze_timeout: {fp} unfrozen after {freeze_timeout} iterations")

        final_best_score, final_best_values, final_best_total_detail, final_best_img = (
            self._resolve_best_snapshot_state(
                best_score=best_score,
                best_values=best_values,
                best_total_detail=best_total_detail,
                best_img=best_img,
            )
        )
        result = self._build_result_payload(
            best_score=final_best_score,
            best_values=final_best_values,
            best_total_detail=final_best_total_detail,
            best_img=final_best_img,
            best_score_image=self._ensure_best_score_image(
                final_best_img,
                final_best_total_detail,
                values=final_best_values,
            ),
            best_overlay_image=self._ensure_best_overlay_image(final_best_img),
            stop_reason=stop_reason,
            history=self._trim_history(history),
            in_progress=False,
        )
        self._print_acceptance_summary(final_best_total_detail)
        self._print_calibration_summary(result["summary"])
        self._write_progress_result(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason=stop_reason,
            history=self._trim_history(history),
            in_progress=False,
        )
        return result

    def _parabolic_optimal_offset(self, p, base_value, base_score, step, direction, evaluate_fn):
        """P8: Auto parabolic interpolation for offset params."""
        if "offset" not in p.name.lower():
            return None
        points = [(0.0, base_score)]
        for mult in [1.0, 2.0]:
            trial_val = base_value + direction * step * mult
            score = evaluate_fn({p.name: trial_val})
            points.append((mult, score))
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        a, b, _ = np.polyfit(xs, ys, 2)
        if a <= 0.0:
            return None
        optimal_mult = -b / (2.0 * a)
        if not 0.5 <= optimal_mult <= 3.0:
            return None
        return base_value + direction * step * optimal_mult
