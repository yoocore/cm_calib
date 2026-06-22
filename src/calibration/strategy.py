"""StrategyMixin — strategy adaptation, bottleneck detection, and exploration profiles."""
from typing import Dict, List, Optional, Tuple

from src.calibration.calib_types import ParameterSpec, TotalScoreDetail


class StrategyMixin:

    def _clamp_strategy_step_scale(self, step_scale: float) -> float:
        return min(self.strategy_max_step_scale, max(self.strategy_min_step_scale, step_scale))

    def _strategy_active_profile(self) -> Dict[str, object]:
        active_profile = self.strategy_exploration_profiles[0]
        for profile in self.strategy_exploration_profiles:
            if self.strategy_stagnation_count < int(profile.get("min_stagnation", 0)):
                break
            active_profile = profile
        return active_profile

    def _strategy_bottleneck_board_ids(self, total_detail: TotalScoreDetail) -> List[str]:
        if not self.strategy_bottleneck_board_awareness:
            return []
        compared = self._compared_board_scores(total_detail)
        if not compared:
            return []
        compared.sort(key=lambda score: score.total_score, reverse=True)
        return [score.board_id for score in compared[: self.strategy_bottleneck_top_k]]

    def _strategy_bottleneck_focus_boost(
        self,
        baseline_detail: Optional[TotalScoreDetail],
        candidate_detail: Optional[TotalScoreDetail],
    ) -> Tuple[float, List[str]]:
        if (
            not self.strategy_bottleneck_board_awareness
            or baseline_detail is None
            or candidate_detail is None
        ):
            return 0.0, []

        bottleneck_ids = self._strategy_bottleneck_board_ids(baseline_detail)
        if not bottleneck_ids:
            return 0.0, []

        baseline_scores = {
            score.board_id: score
            for score in self._compared_board_scores(baseline_detail)
        }
        candidate_scores = {
            score.board_id: score
            for score in self._compared_board_scores(candidate_detail)
        }
        boost = 0.0
        improved_board_ids: List[str] = []
        for rank, board_id in enumerate(bottleneck_ids, start=1):
            baseline_score = baseline_scores.get(board_id)
            candidate_score = candidate_scores.get(board_id)
            if baseline_score is None or candidate_score is None:
                continue
            improvement = baseline_score.total_score - candidate_score.total_score
            if improvement < self.strategy_bottleneck_min_improvement:
                continue
            boost += improvement / float(rank)
            improved_board_ids.append(board_id)
        return boost, improved_board_ids

    def _strategy_effective_step(self, param: ParameterSpec) -> float:
        if not (self.strategy_adaptation_enabled and self.strategy_adjust_step_scale):
            return float(param.step)
        state = self.strategy_param_state.get(param.name, {})
        step_scale = float(state.get("step_scale", 1.0))
        return max(param.min_step, float(param.step) * self._clamp_strategy_step_scale(step_scale))

    def _strategy_iteration_meta(self, ordered_params: List[ParameterSpec]) -> Optional[Dict[str, object]]:
        if not self.strategy_adaptation_enabled:
            return None
        active_profile = self._strategy_active_profile()
        return {
            "stagnation_count": self.strategy_stagnation_count,
            "exploration_profile": str(active_profile.get("name", "baseline")),
            "param_order": [param.name for param in ordered_params],
            "step_scales": {
                param.name: float(self.strategy_param_state.get(param.name, {}).get("step_scale", 1.0))
                for param in ordered_params
            },
            "effective_steps": {
                param.name: self._strategy_effective_step(param)
                for param in ordered_params
            },
            "trial_multipliers": {
                param.name: self._trial_multipliers_for_param(param.name)
                for param in ordered_params
            },
        }

    def _strategy_state_payload(self) -> Dict[str, object]:
        ordered_params = self._ordered_params_for_iteration()
        active_profile = self._strategy_active_profile()
        return {
            "enabled": self.strategy_adaptation_enabled,
            "reorder_params": self.strategy_reorder_params,
            "adjust_step_scale": self.strategy_adjust_step_scale,
            "focus_on_joint_candidates": self.strategy_focus_on_joint_candidates,
            "bottleneck_board_awareness": self.strategy_bottleneck_board_awareness,
            "stagnation_count": self.strategy_stagnation_count,
            "current_exploration_profile": {
                "name": str(active_profile.get("name", "baseline")),
                "min_stagnation": int(active_profile.get("min_stagnation", 0)),
                "single_trial_multipliers": list(active_profile.get("single_trial_multipliers", [1.0])),
                "joint_trial_multipliers": list(active_profile.get("joint_trial_multipliers", [])),
            },
            "current_param_order": [param.name for param in ordered_params],
            "params": {
                param.name: {
                    "priority_score": float(self.strategy_param_state.get(param.name, {}).get("priority_score", 0.0)),
                    "bottleneck_focus_score": float(self.strategy_param_state.get(param.name, {}).get("bottleneck_focus_score", 0.0)),
                    "step_scale": float(self.strategy_param_state.get(param.name, {}).get("step_scale", 1.0)),
                    "effective_step": self._strategy_effective_step(param),
                    "trial_multipliers": self._trial_multipliers_for_param(param.name),
                    "attempt_count": int(self.strategy_param_state.get(param.name, {}).get("attempt_count", 0)),
                    "accepted_count": int(self.strategy_param_state.get(param.name, {}).get("accepted_count", 0)),
                    "joint_candidate_count": int(self.strategy_param_state.get(param.name, {}).get("joint_candidate_count", 0)),
                    "last_bottleneck_boards": list(self.strategy_param_state.get(param.name, {}).get("last_bottleneck_boards", [])),
                    "last_accepted_iteration": self.strategy_param_state.get(param.name, {}).get("last_accepted_iteration"),
                }
                for param in self.params
            },
            "exploration_profiles": [
                {
                    "name": str(profile.get("name", "profile")),
                    "min_stagnation": int(profile.get("min_stagnation", 0)),
                    "single_trial_multipliers": list(profile.get("single_trial_multipliers", [1.0])),
                    "joint_trial_multipliers": list(profile.get("joint_trial_multipliers", [])),
                }
                for profile in self.strategy_exploration_profiles
            ],
        }
