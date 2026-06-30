import argparse
import atexit
import copy
import ctypes
import json
import math
import msvcrt
import os
import random
import re
import shutil
import subprocess
import sys
import time
import warnings
import uuid
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, TextIO, Tuple

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.calibration.calib_types import *
from src.calibration.utils import (
    _unlink_if_exists,
    _default_sim_output_root,
    _sim_output_root_legacy,
    _deep_merge_dict,
    _path_to_json_string,
    _bootstrap_partial_template_dir,
    _is_custom_marker_board_type,
    _is_aruco_family_board_type,
    _is_apriltag_board_type,
    _is_circle_grid_board_type,
    _is_aruco_grid_board_type,
    _derive_camera_name_from_image_path,
    _board_prototype_family,
    _canonical_camera_group_name,
    _camera_name_from_output_dir,
    _quantize_float,
    _format_scalar_value_map,
    _clamp_to_parameter_bounds,
    _resolve_parameter_bounds,
    _build_explicit_parameter_config,
    _build_annotation_legend_lines,
    _DEFAULT_BOUNDS_MULTIPLIER,
)
from src.calibration.config import (
    _default_bootstrap_template_path,
    _default_parameter_config,
    _default_parameter_order,
    _default_bootstrap_config,
    _resolved_bootstrap_config,
    _preprocess_auto_template_match_image,
    _masked_secondary_response_max,
    _select_auto_template_crop,
    _materialize_auto_template_image,
    _get_annotation_ocr_engine,
    _normalize_annotation_board_id,
    _run_annotation_ocr,
    _rect_gap_distance,
    _assign_annotation_board_ids,
    _extract_annotation_board_ids,
    _extract_annotation_rectangles,
    _cluster_1d,
    _group_annotation_rectangles,
    _load_bootstrap_template_specs,
    _build_boards_from_annotation_rectangles,
    _auto_upgrade_partial_checkerboards,
    _sync_materialized_board_fields_from_calibrator,
    bootstrap_config_from_annotation,
)
import cv2
import numpy as np
from PIL import Image

from src.health.precheck_cli import run_precheck

from src.health.dde_health_check import (
    default_output_dir as _dde_default_output_dir,
    render_dde_execute_script,
    render_result_script,
    run_check_attempt,
)

from src.calibration.detector import DetectorMixin
from src.calibration.scoring import ScoringMixin
from src.calibration.annotation import AnnotationMixin

from src.calibration.script_control import ScriptControlMixin
from src.calibration.evaluate import EvaluateMixin
from src.calibration.optimizer_cd import CoordinateDescentMixin
from src.calibration.optimizer_bayesian import BayesianOptimizerMixin

from src.calibration.strategy import StrategyMixin
from src.calibration.orchestration import OrchestrationMixin
from src.calibration.utils import _TeeStream



_DDE_RECOVERY_ERROR_MARKERS = (
    "remote server cannot handle this command",
    "timed out waiting for",
    "did not execute",
    "exec failed",
    "dde dispatch circuit recovery",
)


# Re-imported from orchestration.py (where runtime orchestration functions now live)
from src.calibration.orchestration import (
    _camera_name_from_config_path,
    _camera_scope_output_dir,
    _configure_live_log_for_output_dir,
    _resolve_config_output_dir,
    _resolve_score_scope_from_cfg,
)

class CameraCalibrator(DetectorMixin, ScoringMixin, AnnotationMixin, ScriptControlMixin, EvaluateMixin, CoordinateDescentMixin, BayesianOptimizerMixin, StrategyMixin, OrchestrationMixin):
    DDE_OPERATION_TIMEOUT_FLOOR_SEC = 20.0
    SCRIPT_CONTROL_WRITE_WIDGETS = {
        "roll": ".camera.presetFrame.x",
        "pitch": ".camera.presetFrame.y",
        "yaw": ".camera.presetFrame.z",
        "pos_x": ".camera.presetFrame.evptx",
        "pos_y": ".camera.presetFrame.evpty",
        "pos_z": ".camera.presetFrame.evptz",
        "fov": ".camera.fovFrame.efov",
        "lens_fov": ".camera.cammoddlg.fov.e",
        "lens_scale": ".camera.cammoddlg.fisheye.ctrl.e1",
        "lens_offset_x": ".camera.cammoddlg.fisheye.ctrl.e2",
        "lens_offset_y": ".camera.cammoddlg.fisheye.ctrl.e3",
    }
    SCRIPT_CONTROL_READ_WIDGETS = {
        "roll": ".camera.presetFrame.x",
        "pitch": ".camera.presetFrame.y",
        "yaw": ".camera.presetFrame.z",
        "pos_x": ".camera.presetFrame.svptx",
        "pos_y": ".camera.presetFrame.svpty",
        "pos_z": ".camera.presetFrame.svptz",
        "fov": ".camera.fovFrame.efov",
        "lens_fov": ".camera.cammoddlg.fov.e",
        "lens_scale": ".camera.cammoddlg.fisheye.ctrl.e1",
        "lens_offset_x": ".camera.cammoddlg.fisheye.ctrl.e2",
        "lens_offset_y": ".camera.cammoddlg.fisheye.ctrl.e3",
    }
    SCRIPT_CONTROL_READ_DECIMALS = {
        "pos_x": 3,
        "pos_y": 3,
        "pos_z": 3,
    }

    def __init__(self, cfg: dict, config_path: Optional[Path] = None):
        self.cfg = cfg
        self.config_path = config_path
        self.repo_root = Path(__file__).resolve().parents[2]
        self.cmapi_host = str(cfg.get("cmapi_host", "localhost"))
        default_cm_install_root = Path(os.environ.get("IPGHOME", "D:/IPG")) / "carmaker" / "win64-14.1"
        self.cm_install_root = Path(str(cfg.get("cm_install_root", default_cm_install_root)))
        self.movie_apphost = str(cfg.get("movie_apphost", "kel")).strip() or "kel"
        self.score_scope = _resolve_score_scope_from_cfg(cfg)
        self.output_dir = _resolve_config_output_dir(cfg, config_path)
        cfg["output_dir"] = str(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.real_image_path = Path(cfg["real_image"]).resolve()
        self.camera_name = _derive_camera_name_from_image_path(self.real_image_path)
        if not self.camera_name:
            self.camera_name = _camera_name_from_config_path(config_path)
        cache_name = re.sub(r"[^A-Za-z0-9_]+", "_", self.camera_name or "camera").strip("_") or "camera"
        camera_scope_dir = _camera_scope_output_dir(self.output_dir)
        self.movie_size_cache_path = camera_scope_dir / f"{cache_name.lower()}_movie_size_cache.txt"

        self.real_img = cv2.imread(cfg["real_image"], cv2.IMREAD_GRAYSCALE)
        if self.real_img is None:
            raise FileNotFoundError(f"Cannot read real image: {cfg['real_image']}")
        self.real_img_color = cv2.imread(cfg["real_image"], cv2.IMREAD_COLOR)
        if self.real_img_color is None:
            raise FileNotFoundError(f"Cannot read real image in color: {cfg['real_image']}")
        # Compute expected FBO dimensions (auto-reduced to fit display, matching ensure_movie_view_size)
        try:
            _dw = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
            _dh = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
        except Exception:
            _dw, _dh = 1920, 1080
        _fw = int(self.real_img.shape[1])
        _fh = int(self.real_img.shape[0])
        while _fw > _dw - 50 or _fh > _dh - 50:
            _fw //= 2
            _fh //= 2
            if _fw < 64 or _fh < 64:
                break
        self._capture_width = max(1, _fw)
        self._capture_height = max(1, _fh)

        self.orb = cv2.ORB_create(nfeatures=3000)
        self.params = self._load_params(cfg["parameters"])
        self.params = self._order_params(self.params, cfg.get("optimization_order"))

        self.settle_sec = float(cfg.get("settle_sec", 0.3))
        self.target_score = float(cfg.get("target_score", 5.0))
        acceptance_cfg = cfg.get("acceptance_criteria", {})
        self.bottleneck_board_score_max_threshold = float(
            acceptance_cfg.get("bottleneck_board_score_max_threshold", 4.0)
        )
        self.bottleneck_board_score_avg_threshold = float(
            acceptance_cfg.get("bottleneck_board_score_avg_threshold", 2.5)
        )
        self.max_iters = int(cfg.get("max_iters", 100))
        self.min_improve = float(cfg.get("min_improve", 1e-4))
        self.step_decay = float(cfg.get("step_decay", 0.6))
        self.settings_input_mode = str(cfg.get("settings_input_mode", "script_control")).lower()
        if self.settings_input_mode != "script_control":
            raise ValueError("Only settings_input_mode='script_control' is supported")
        calibration_root = Path(__file__).resolve().parent
        default_command_path = calibration_root / "script_control_apply.tcl"
        default_result_path = self.repo_root / "SimOutput" / "script_control_camera_apply_result.txt"
        configured_script_path = Path(cfg.get("script_control_script_path", str(default_command_path)))
        if not configured_script_path.is_absolute():
            configured_script_path = (self.repo_root / configured_script_path).resolve()
        self.script_control_template_path = configured_script_path
        runtime_script_name = (
            f"{configured_script_path.stem}.runtime{configured_script_path.suffix}"
            if configured_script_path.suffix
            else f"{configured_script_path.name}.runtime"
        )
        self.script_control_script_path = self.output_dir / runtime_script_name
        self.script_control_result_path = Path(
            cfg.get("script_control_result_path", str(default_result_path))
        )
        self.script_control_dde_service = str(cfg.get("script_control_dde_service", "TclEval"))
        self.script_control_dde_topic = str(cfg.get("script_control_dde_topic", "CarMaker"))
        configured_script_control_timeout_sec = float(cfg.get("script_control_timeout_sec", 5.0))
        self.script_control_timeout_sec = max(
            configured_script_control_timeout_sec,
            self.DDE_OPERATION_TIMEOUT_FLOOR_SEC,
        )
        self.script_control_settle_sec = float(cfg.get("script_control_settle_sec", 0.2))
        self.dde_circuit_trip_failures = max(1, int(cfg.get("dde_circuit_trip_failures", 3)))
        self.dde_circuit_cooldown_sec = max(
            0.0,
            float(cfg.get("dde_circuit_cooldown_sec", 1.5)),
        )
        # Optional FBO size override for minimized window support (set by caller)
        self._capture_width: Optional[int] = None
        self._capture_height: Optional[int] = None
        self.dde_dispatch_failure_streak = 0
        self.dde_circuit_opened_at: Optional[float] = None
        self.dde_circuit_last_error_text = ""
        self._dde_recovery_probe_active = False
        self.verbose_dde_diag = bool(cfg.get("verbose_dde_diag", False))
        self.movie_restart_settle_sec = float(cfg.get("movie_restart_settle_sec", 2.0))
        self.max_gui_movie_restart_recoveries = max(
            0,
            int(cfg.get("max_gui_movie_restart_recoveries", 1)),
        )
        self.gui_movie_restart_recovery_attempts = 0
        self.template_feature_max_dim = int(cfg.get("template_feature_max_dim", 2048))
        self.comparison_mode = str(cfg.get("comparison_mode", "direct")).lower()
        if self.comparison_mode not in {"direct", "overlay_residual"}:
            raise ValueError("comparison_mode must be 'direct' or 'overlay_residual'")
        self.overlay_residual_threshold = int(cfg.get("overlay_residual_threshold", 12))
        self.overlay_residual_blur = int(cfg.get("overlay_residual_blur", 0))
        self.verify_all_coordinate_fields = bool(
            cfg.get("verify_all_coordinate_fields", True)
        )
        self.stop_after_first_accepted_direction = bool(
            cfg.get("stop_after_first_accepted_direction", True)
        )
        priority_accept_cfg = cfg.get("priority_board_acceptance", {})
        self.priority_board_accept_ids = [
            str(board_id).strip()
            for board_id in priority_accept_cfg.get("board_ids", [])
            if str(board_id).strip()
        ]
        self.priority_board_accept_min_improvement = float(
            priority_accept_cfg.get("min_board_score_improvement", 0.0)
        )
        self.priority_board_accept_max_total_worsen = float(
            priority_accept_cfg.get("max_total_score_worsen", 0.0)
        )
        self.priority_board_accept_min_total_improvement = float(
            priority_accept_cfg.get("min_total_board_score_improvement", 0.0)
        )
        self.priority_board_accept_worsen_tradeoff_ratio = max(
            0.0, float(priority_accept_cfg.get("total_worsen_tradeoff_ratio", 0.0))
        )
        self.priority_board_accept_min_count = max(
            1, int(priority_accept_cfg.get("min_board_count", 1))
        )
        objective_board_focus_cfg = cfg.get("objective_board_focus", {})
        self.objective_board_focus_enabled = bool(
            objective_board_focus_cfg.get("enabled", False)
        )
        self.objective_board_focus_top_k = max(
            1, int(objective_board_focus_cfg.get("top_k", 3))
        )
        self.objective_board_focus_score_threshold = float(
            objective_board_focus_cfg.get("score_threshold", 8.0)
        )
        raw_focus_board_ids = objective_board_focus_cfg.get("focus_board_ids", [])
        self.objective_board_focus_board_ids = {
            str(board_id).strip()
            for board_id in raw_focus_board_ids
            if str(board_id).strip()
        }
        self.objective_board_focus_rank_multipliers = self._normalize_trial_multiplier_values(
            objective_board_focus_cfg.get("rank_multipliers", [1.35, 1.2, 1.1]),
            [1.35, 1.2, 1.1],
        )
        raw_type_multipliers = objective_board_focus_cfg.get("board_type_multipliers", {})
        self.objective_board_focus_type_multipliers: Dict[str, float] = {}
        if isinstance(raw_type_multipliers, dict):
            for raw_type, raw_value in raw_type_multipliers.items():
                board_type = str(raw_type).strip().lower()
                if not board_type:
                    continue
                try:
                    self.objective_board_focus_type_multipliers[board_type] = max(
                        1.0, float(raw_value)
                    )
                except Exception:
                    continue
        self.objective_board_focus_priority_board_multiplier = max(
            1.0, float(objective_board_focus_cfg.get("priority_board_multiplier", 1.05))
        )
        isolated_board_guard_cfg = cfg.get("isolated_board_guard", {})
        self.isolated_board_guard_enabled = bool(
            isolated_board_guard_cfg.get("enabled", True)
        )
        self.isolated_board_guard_abs_score_threshold = float(
            isolated_board_guard_cfg.get("abs_score_threshold", 60.0)
        )
        self.isolated_board_guard_baseline_ratio = max(
            1.0, float(isolated_board_guard_cfg.get("baseline_ratio_threshold", 6.0))
        )
        self.isolated_board_guard_peer_ratio = max(
            1.0, float(isolated_board_guard_cfg.get("peer_ratio_threshold", 4.0))
        )
        self.isolated_board_guard_min_other_boards = max(
            2, int(isolated_board_guard_cfg.get("min_other_boards", 5))
        )
        self.isolated_board_guard_max_boards = max(
            1, int(isolated_board_guard_cfg.get("max_quarantined_boards", 1))
        )
        self.degrade_lambda = float(cfg.get("degrade_lambda", 100.0))
        self.compare_only_if_reference_visible = bool(
            cfg.get("compare_only_if_reference_visible", True)
        )
        self.no_signal_penalty = float(cfg.get("no_signal_penalty", 1e5))
        self.progress_flush_every = max(1, int(cfg.get("progress_flush_every", 1)))
        self.max_history_entries = max(0, int(cfg.get("max_history_entries", 10000)))
        self.optimizer_mode = str(cfg.get("optimizer_mode", "auto")).lower()
        if self.optimizer_mode not in {"coordinate_descent", "bayesian", "auto", "hybrid"}:
            raise ValueError(
                "optimizer_mode must be 'coordinate_descent', 'bayesian', 'auto', or 'hybrid'"
            )
        self.use_gauss_newton = bool(cfg.get("use_gauss_newton", True))
        self.strategy_adaptation = bool(cfg.get("strategy_adaptation", True))
        self.jitter_eps = float(cfg.get("jitter_eps", 0.01))
        self.jitter_decay = float(cfg.get("jitter_decay", 0.98))
        self.hybrid_phase1_iters = int(cfg.get("hybrid_phase1_iters", 15))
        self.curriculum_annealing = bool(cfg.get("curriculum_annealing", False))
        self.parabolic_refinement = bool(cfg.get("parabolic_refinement", False))
        self.keep_aspect_resize = bool(cfg.get("keep_aspect_resize", True))
        self.auto_generate_best_score_image = bool(
            cfg.get("auto_generate_best_score_image", True)
        )
        self.auto_generate_best_overlay_image = bool(
            cfg.get("auto_generate_best_overlay_image", True)
        )
        self.overlay_visual_real_alpha = float(cfg.get("overlay_visual_real_alpha", 0.45))
        self.overlay_visual_real_alpha = min(1.0, max(0.0, self.overlay_visual_real_alpha))
        joint_exploration_cfg = cfg.get("joint_exploration", {})
        configured_joint_param_names = [
            str(name).strip()
            for name in joint_exploration_cfg.get("param_names", [])
            if str(name).strip()
        ]
        self.joint_exploration_apply_to_all_params = bool(
            joint_exploration_cfg.get(
                "apply_to_all_params",
                joint_exploration_cfg.get("all_params", False),
            )
        )
        if self.joint_exploration_apply_to_all_params:
            self.joint_exploration_param_names = [param.name for param in self.params]
        else:
            self.joint_exploration_param_names = configured_joint_param_names
        self.joint_exploration_param_set = set(self.joint_exploration_param_names)
        self.joint_exploration_max_single_worsen = float(
            joint_exploration_cfg.get("max_single_score_worsen", 0.0)
        )
        raw_trial_multipliers = joint_exploration_cfg.get("trial_multipliers", [1.0])
        trial_multipliers: List[float] = []
        for raw_value in raw_trial_multipliers:
            try:
                multiplier = abs(float(raw_value))
            except (TypeError, ValueError):
                continue
            if multiplier <= 0.0:
                continue
            if any(math.isclose(multiplier, seen, rel_tol=0.0, abs_tol=1e-12) for seen in trial_multipliers):
                continue
            trial_multipliers.append(multiplier)
        self.joint_exploration_trial_multipliers = trial_multipliers or [1.0]
        self.param_order_index = {param.name: index for index, param in enumerate(self.params)}
        self.preferred_directions = {param.name: 1.0 for param in self.params}
        strategy_cfg = cfg.get("strategy_adaptation", {})
        self.strategy_adaptation_enabled = bool(strategy_cfg.get("enabled", False))
        self.strategy_reorder_params = bool(strategy_cfg.get("reorder_params", True))
        self.strategy_adjust_step_scale = bool(strategy_cfg.get("adjust_step_scale", True))
        self.strategy_focus_on_joint_candidates = bool(
            strategy_cfg.get("focus_on_joint_candidates", True)
        )
        self.strategy_bottleneck_board_awareness = bool(
            strategy_cfg.get("bottleneck_board_awareness", True)
        )
        self.strategy_bottleneck_top_k = max(
            1,
            int(strategy_cfg.get("bottleneck_top_k", 2)),
        )
        self.strategy_bottleneck_min_improvement = max(
            0.0,
            float(strategy_cfg.get("bottleneck_min_improvement", 0.1)),
        )
        self.strategy_bottleneck_priority_boost = max(
            0.0,
            float(strategy_cfg.get("bottleneck_priority_boost", 1.25)),
        )
        self.strategy_priority_decay = min(
            1.0,
            max(0.0, float(strategy_cfg.get("priority_decay", 0.82))),
        )
        self.strategy_accepted_priority_boost = float(
            strategy_cfg.get("accepted_priority_boost", 2.5)
        )
        self.strategy_joint_candidate_priority_boost = float(
            strategy_cfg.get("joint_candidate_priority_boost", 0.75)
        )
        self.strategy_rejected_priority_penalty = float(
            strategy_cfg.get("rejected_priority_penalty", 0.15)
        )
        self.strategy_step_scale_up = max(
            1.0,
            float(strategy_cfg.get("step_scale_up", 1.35)),
        )
        self.strategy_step_scale_down = min(
            1.0,
            max(0.05, float(strategy_cfg.get("step_scale_down", 0.85))),
        )
        self.strategy_stagnation_patience = max(
            1,
            int(strategy_cfg.get("stagnation_patience", 2)),
        )
        self.strategy_stagnation_step_scale_up = max(
            1.0,
            float(strategy_cfg.get("stagnation_step_scale_up", 1.2)),
        )
        self.strategy_min_step_scale = max(
            0.05,
            float(strategy_cfg.get("min_step_scale", 0.5)),
        )
        self.strategy_max_step_scale = max(
            self.strategy_min_step_scale,
            float(strategy_cfg.get("max_step_scale", 3.0)),
        )
        default_profiles = [
            {
                "name": "baseline",
                "min_stagnation": 0,
                "single_trial_multipliers": [1.0],
                "joint_trial_multipliers": [],
            },
            {
                "name": "expanded",
                "min_stagnation": 2,
                "single_trial_multipliers": [1.0, 2.0],
                "joint_trial_multipliers": [6.0],
            },
            {
                "name": "aggressive",
                "min_stagnation": 4,
                "single_trial_multipliers": [1.0, 2.0, 4.0],
                "joint_trial_multipliers": [6.0, 8.0],
            },
        ]
        raw_profiles = strategy_cfg.get("exploration_profiles", default_profiles)
        if not isinstance(raw_profiles, list):
            raw_profiles = default_profiles
        self.strategy_exploration_profiles: List[Dict[str, object]] = []
        for index, raw_profile in enumerate(raw_profiles):
            if not isinstance(raw_profile, dict):
                continue
            profile_name = str(raw_profile.get("name", f"profile_{index}")).strip()
            if not profile_name:
                profile_name = f"profile_{index}"
            self.strategy_exploration_profiles.append(
                {
                    "name": profile_name,
                    "min_stagnation": max(0, int(raw_profile.get("min_stagnation", 0))),
                    "single_trial_multipliers": self._normalize_trial_multiplier_values(
                        raw_profile.get("single_trial_multipliers", [1.0]),
                        default=[1.0],
                    ),
                    "joint_trial_multipliers": self._normalize_trial_multiplier_values(
                        raw_profile.get("joint_trial_multipliers", []),
                        default=[],
                    ),
                }
            )
        if not self.strategy_exploration_profiles:
            self.strategy_exploration_profiles = default_profiles
        self.strategy_exploration_profiles.sort(
            key=lambda profile: int(profile.get("min_stagnation", 0))
        )
        self.strategy_stagnation_count = 0
        self.strategy_param_state = {
            param.name: {
                "priority_score": 0.0,
                "step_scale": 1.0,
                "attempt_count": 0,
                "accepted_count": 0,
                "joint_candidate_count": 0,
                "bottleneck_focus_score": 0.0,
                "last_bottleneck_boards": [],
                "last_accepted_iteration": None,
            }
            for param in self.params
        }
        self.resume_result_path: Optional[Path] = None
        self.resume_best_score: Optional[float] = None
        self.live_log_path: Optional[Path] = None
        self.run_session_id = uuid.uuid4().hex
        self.run_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.run_started_perf = time.perf_counter()
        self._best_score_image_cache: Dict[str, Path] = {}
        self._best_overlay_image_cache: Dict[str, Path] = {}
        self._total_trial_count: int = 0
        self._total_iteration_count: int = 0
        self._calib_phase: str = ""
        self._calib_dir_index: int = 0
        self._calib_total_dirs: int = 0
        self._calib_max_iters: int = 0
        self._calib_round_index: int = 0
        self._calib_round_count: int = 0
        self._calib_overall_total_iters: int = 0
        self._trial_log_path: Path = self.output_dir / "trial_log.jsonl"

        self.boards = self._load_boards(cfg.get("boards", []))
        if not self.boards:
            raise ValueError("boards must be a non-empty array")

        self._materialize_custom_maker_templates()

        self.custom_templates = self._load_custom_templates(self.boards)
        self.real_detections: Optional[Dict[str, DetectionResult]] = None
        self._sim_templates_generated: bool = False
        self._sim_sourced_board_ids: Set[str] = set()

    def _materialize_custom_maker_templates(self) -> None:
        template_dir = _bootstrap_partial_template_dir(self.real_image_path, self.camera_name)
        for board in self.boards:
            if board.board_type != "custom_maker" or board.template_image or board.roi is None:
                continue
            template_source_roi = board.template_source_roi or board.roi
            manual_crop = board.template_source_crop
            if manual_crop is None:
                _, _, source_w, source_h = template_source_roi
                manual_crop = (0, 0, int(source_w), int(source_h))
            template_name = re.sub(r"[^A-Za-z0-9_]+", "_", board.board_id).strip("_") or "custom_maker"
            template_path = template_dir / f"{template_name.lower()}_auto.png"
            template_path, template_source_crop = _materialize_auto_template_image(
                self.real_img,
                template_source_roi,
                board.template_binary_threshold,
                template_path,
                manual_crop=manual_crop,
            )
            board.template_image = _path_to_json_string(template_path)
            board.template_source_crop = template_source_crop

    @staticmethod
    def _read_clipboard_text() -> str:
        # Prefer pywin32 clipboard API when available.
        text = ""
        try:
            import win32clipboard  # type: ignore

            for _ in range(3):
                try:
                    win32clipboard.OpenClipboard()
                    data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    text = data if isinstance(data, str) else str(data)
                    win32clipboard.CloseClipboard()
                    return text.strip()
                except Exception:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass
                    time.sleep(0.05)
            return text.strip()
        except Exception:
            pass

        # Fallback via Win32 APIs.
        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        for _ in range(3):
            if user32.OpenClipboard(None):
                try:
                    handle = user32.GetClipboardData(CF_UNICODETEXT)
                    if handle:
                        ptr = kernel32.GlobalLock(handle)
                        if ptr:
                            try:
                                text = ctypes.wstring_at(ptr)
                                return text.strip()
                            finally:
                                kernel32.GlobalUnlock(handle)
                finally:
                    user32.CloseClipboard()
            time.sleep(0.05)
        return text.strip()

    @staticmethod
    def _clear_clipboard_text() -> None:
        try:
            import win32clipboard  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
            finally:
                win32clipboard.CloseClipboard()
            return
        except Exception:
            pass

    @staticmethod
    def _set_clipboard_text(text: str) -> None:
        try:
            import win32clipboard  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return
        except Exception:
            pass

        GMEM_MOVEABLE = 0x0002
        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        data = ctypes.create_unicode_buffer(text + "\0")
        byte_size = ctypes.sizeof(data)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, byte_size)
        if not handle:
            raise RuntimeError("GlobalAlloc failed while setting clipboard text")
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            raise RuntimeError("GlobalLock failed while setting clipboard text")
        try:
            ctypes.memmove(ptr, ctypes.addressof(data), byte_size)
        finally:
            kernel32.GlobalUnlock(handle)

        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            raise RuntimeError("OpenClipboard failed while setting clipboard text")
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise RuntimeError("SetClipboardData failed while setting clipboard text")
            handle = None
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

        try:
            user32 = ctypes.windll.user32
            if user32.OpenClipboard(None):
                try:
                    user32.EmptyClipboard()
                finally:
                    user32.CloseClipboard()
        except Exception:
            pass

    @staticmethod
    def _order_params(
        params: List[ParameterSpec], optimization_order: Optional[List[str]]
    ) -> List[ParameterSpec]:
        if not optimization_order:
            return params

        param_map = {param.name: param for param in params}
        ordered: List[ParameterSpec] = []
        used = set()
        for name in optimization_order:
            if name in param_map:
                ordered.append(param_map[name])
                used.add(name)
        for param in params:
            if param.name not in used:
                ordered.append(param)
        return ordered

    @staticmethod
    def _parse_roi(roi_cfg: Optional[List[int]]) -> Optional[Tuple[int, int, int, int]]:
        if roi_cfg is None:
            return None
        if not isinstance(roi_cfg, list) or len(roi_cfg) != 4:
            raise ValueError("roi must be [x, y, width, height]")
        x, y, width, height = [int(v) for v in roi_cfg]
        if width <= 0 or height <= 0:
            raise ValueError("roi width and height must be positive")
        return (x, y, width, height)

    @staticmethod
    def _read_float(v, default: float) -> float:
        if v is None:
            return float(default)
        return float(v)

    @staticmethod
    def _read_int(v, default: int) -> int:
        if v is None:
            return int(default)
        return int(v)

    @staticmethod
    def _read_crop_box(v) -> Optional[Tuple[int, int, int, int]]:
        if v is None:
            return None
        if not isinstance(v, list) or len(v) != 4:
            raise ValueError("template_crop must be [x, y, width, height]")
        x, y, width, height = [int(item) for item in v]
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("template_crop values must be non-negative with positive width/height")
        return (x, y, width, height)

    def _load_boards(self, boards_cfg: List[dict]) -> List[BoardProfile]:
        boards: List[BoardProfile] = []
        for board in boards_cfg:
            board_id = str(board.get("board_id", "")).strip()
            board_type = str(board.get("board_type", "")).strip().lower()
            if not board_id:
                raise ValueError("Each board must provide board_id")
            if board_type not in {
                "checkerboard",
                "custom_groundmaker",
                "custom_maker",
                "aruco",
                "charuco",
                "apriltag",
                "circle_grid",
                "aruco_grid",
            }:
                raise ValueError(f"Unsupported board_type for {board_id}: {board_type}")

            roi = self._parse_roi(board.get("roi"))
            template_source_roi = self._parse_roi(board.get("template_source_roi"))
            template_source_crop = self._parse_roi(board.get("template_source_crop"))
            board_size = None
            if board_type in {"checkerboard", "charuco", "circle_grid", "aruco_grid"}:
                raw_size = board.get("board_size")
                if not isinstance(raw_size, list) or len(raw_size) != 2:
                    raise ValueError(
                        f"{board_type} {board_id} must provide board_size=[cols, rows]"
                    )
                board_size = (int(raw_size[0]), int(raw_size[1]))
                if board_size[0] < 2 or board_size[1] < 2:
                    raise ValueError(f"{board_type} {board_id} board_size must be >= [2, 2]")

            if board_type == "checkerboard":
                min_points_default = board_size[0] * board_size[1] if board_size else 6
            elif board_type == "charuco":
                charuco_corner_count = max(1, (board_size[0] - 1) * (board_size[1] - 1))
                min_points_default = max(4, min(charuco_corner_count, 12))
            elif board_type == "aruco":
                min_points_default = 8
            elif board_type == "circle_grid":
                min_points_default = board_size[0] * board_size[1] if board_size else 6
            elif board_type == "aruco_grid":
                min_points_default = board_size[0] * board_size[1] * 4 if board_size else 8
            else:
                min_points_default = 6
            default_detector = "template_match" if board_type == "custom_maker" else "feature"
            custom_detector = str(board.get("custom_detector", default_detector)).strip().lower()
            if custom_detector not in {"feature", "template_match"}:
                raise ValueError(
                    f"Unsupported custom_detector for {board_id}: {custom_detector}"
                )
            aruco_dictionary = str(board.get("aruco_dictionary", "DICT_4X4_50")).strip().upper()
            if _is_aruco_family_board_type(board_type):
                if not aruco_dictionary:
                    raise ValueError(f"{board_type} {board_id} must provide aruco_dictionary")
                self._resolve_aruco_dictionary(aruco_dictionary)
            marker_length_ratio = self._read_float(board.get("marker_length_ratio"), 0.7)
            if board_type == "charuco" and not (0.0 < marker_length_ratio < 1.0):
                raise ValueError(
                    f"charuco {board_id} marker_length_ratio must be between 0 and 1"
                )
            boards.append(
                BoardProfile(
                    board_id=board_id,
                    board_type=board_type,
                    weight=self._read_float(board.get("weight"), 1.0),
                    critical=bool(board.get("critical", True)),
                    roi=roi,
                    detect_roi_padding=self._read_int(board.get("detect_roi_padding"), 0),
                    template_source_roi=template_source_roi,
                    template_source_crop=template_source_crop,
                    board_size=board_size,
                    square_size=self._read_float(board.get("square_size"), 1.0),
                    alpha=self._read_float(board.get("alpha"), 1000.0),
                    beta=self._read_float(board.get("beta"), 0.1),
                    fail_penalty=self._read_float(board.get("fail_penalty"), 1e6),
                    min_detected_points=self._read_int(
                        board.get("min_detected_points"), min_points_default
                    ),
                    degrade_threshold_rmse=self._read_float(
                        board.get("degrade_threshold_rmse"), float("inf")
                    ),
                    degrade_threshold_max_error=self._read_float(
                        board.get("degrade_threshold_max_error"), float("inf")
                    ),
                    degrade_threshold_miss_rate=self._read_float(
                        board.get("degrade_threshold_miss_rate"), float("inf")
                    ),
                    template_image=board.get("template_image"),
                    min_match_count=self._read_int(board.get("min_match_count"), 20),
                    custom_detector=custom_detector,
                    template_match_threshold=self._read_float(
                        board.get("template_match_threshold"), 0.0
                    ),
                    template_binary_threshold=self._read_int(
                        board.get("template_binary_threshold"), 0
                    ),
                    template_crop=self._read_crop_box(board.get("template_crop")),
                    aruco_dictionary=aruco_dictionary,
                    marker_length_ratio=marker_length_ratio,
                    tag_family=str(board.get("tag_family", "tagStandard41h12")).strip(),
                    grid_type=str(board.get("grid_type", "symmetric")).strip().lower(),
                    marker_separation=self._read_float(board.get("marker_separation"), 0.0),
                )
            )
        return boards

    def _load_custom_templates(self, boards: List[BoardProfile]) -> Dict[str, dict]:
        templates: Dict[str, dict] = {}
        template_cache: Dict[Tuple[str, Optional[Tuple[int, int, int, int]], str], dict] = {}
        for board in boards:
            if not board.template_image:
                continue
            cache_key = (
                str(board.template_image),
                board.template_crop,
                board.custom_detector,
                board.board_type,
                board.roi,
                board.template_source_crop,
                board.template_source_roi,
            )
            template_info = template_cache.get(cache_key)
            if template_info is None:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                        with Image.open(str(board.template_image)) as template_image:
                            if (
                                template_image.mode == "P"
                                and "transparency" in template_image.info
                            ):
                                template_image = template_image.convert("RGBA")
                            template_gray = np.ascontiguousarray(
                                np.array(template_image.convert("L"), dtype=np.uint8)
                            )
                except FileNotFoundError:
                    raise FileNotFoundError(
                        f"Cannot read template_image for {board.board_id}: {board.template_image}"
                    )
                except OSError as exc:
                    raise RuntimeError(
                        f"Failed reading template_image for {board.board_id}: {board.template_image}"
                    ) from exc
                if (
                    board.board_type == "checkerboard"
                    and board.template_crop is None
                    and board.roi is not None
                ):
                    crop_x, crop_y, crop_w, crop_h = board.roi
                    crop_x1 = crop_x + crop_w
                    crop_y1 = crop_y + crop_h
                    if crop_x1 <= template_gray.shape[1] and crop_y1 <= template_gray.shape[0]:
                        template_gray = template_gray[crop_y:crop_y1, crop_x:crop_x1]
                if board.template_crop is not None:
                    crop_x, crop_y, crop_w, crop_h = board.template_crop
                    crop_x1 = crop_x + crop_w
                    crop_y1 = crop_y + crop_h
                    if crop_x1 > template_gray.shape[1] or crop_y1 > template_gray.shape[0]:
                        raise ValueError(
                            f"template_crop is outside template image for {board.board_id}: {board.template_crop}"
                        )
                    template_gray = template_gray[crop_y:crop_y1, crop_x:crop_x1]
                if board.custom_detector == "feature" and board.board_type != "checkerboard":
                    max_dim = max(template_gray.shape[:2])
                    if max_dim > self.template_feature_max_dim:
                        scale = float(self.template_feature_max_dim) / float(max_dim)
                        new_width = max(1, int(round(template_gray.shape[1] * scale)))
                        new_height = max(1, int(round(template_gray.shape[0] * scale)))
                        template_gray = cv2.resize(
                            template_gray,
                            (new_width, new_height),
                            interpolation=cv2.INTER_AREA,
                        )
                h, w = template_gray.shape[:2]
                anchor_points = np.array(
                    [
                        [0.0, 0.0],
                        [w - 1.0, 0.0],
                        [w - 1.0, h - 1.0],
                        [0.0, h - 1.0],
                        [w * 0.5, h * 0.5],
                        [w * 0.25, h * 0.25],
                        [w * 0.75, h * 0.25],
                        [w * 0.75, h * 0.75],
                        [w * 0.25, h * 0.75],
                    ],
                    dtype=np.float32,
                )
                template_info = {
                    "template": template_gray,
                    "anchors": anchor_points,
                }
                if _is_custom_marker_board_type(board.board_type):
                    match_template, match_crop = _select_auto_template_crop(
                        template_gray,
                        int(board.template_binary_threshold),
                    )
                    template_info["match_template"] = match_template
                    template_info["match_crop"] = match_crop
                    _, content_mask = cv2.threshold(
                        template_gray,
                        0,
                        255,
                        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
                    )
                    content_points = np.column_stack(np.where(content_mask > 0))
                    if content_points.size > 0:
                        min_y = int(np.min(content_points[:, 0]))
                        max_y = int(np.max(content_points[:, 0]))
                        min_x = int(np.min(content_points[:, 1]))
                        max_x = int(np.max(content_points[:, 1]))
                        template_info["content_bbox"] = (
                            min_x,
                            min_y,
                            max_x - min_x + 1,
                            max_y - min_y + 1,
                        )
                if board.custom_detector == "feature" and board.board_type != "checkerboard":
                    kp, des = self.orb.detectAndCompute(template_gray, None)
                    if des is None or len(kp) < 20:
                        raise RuntimeError(
                            f"Not enough template features for {board.board_id} in {board.template_image}"
                        )
                    template_info["kp"] = kp
                    template_info["des"] = des
                template_cache[cache_key] = template_info
            templates[board.board_id] = template_info
        return templates

    @staticmethod
    def _load_params(param_cfg: Dict[str, dict]) -> List[ParameterSpec]:
        params: List[ParameterSpec] = []
        for name, p in param_cfg.items():
            initial_value = float(p.get("initial", 0.0))
            bounds_multiplier = float(p.get("bounds_multiplier", _DEFAULT_BOUNDS_MULTIPLIER))
            step = float(p.get("step", 0.001))
            half_range = step * bounds_multiplier
            min_value = initial_value - half_range
            max_value = initial_value + half_range
            if min_value > max_value:
                raise ValueError(
                    f"Parameter {name} has invalid range: min ({min_value}) > max ({max_value})"
                )
            if initial_value < min_value or initial_value > max_value:
                raise ValueError(
                    f"Parameter {name} initial ({initial_value}) is outside range "
                    f"[{min_value}, {max_value}]. Update config min/max to include initial."
                )
            params.append(
                ParameterSpec(
                    name=name,
                    value=initial_value,
                    step=float(p["step"]),
                    min_value=min_value,
                    max_value=max_value,
                    min_step=float(p.get("min_step", 0.001)),
                    decimals=int(p.get("decimals", 4)),
                )
            )
        return params


    @staticmethod
    def _format_value_map(values: Dict[str, float]) -> str:
        ordered = []
        for name in sorted(values.keys()):
            ordered.append(f"{name}={values[name]:.4f}")
        return ", ".join(ordered)

    @staticmethod
    def _format_value_lines(
        values: Dict[str, float], items_per_line: int = 3
    ) -> List[str]:
        if not values:
            return ["params=none"]

        items = [f"{name}={values[name]:.4f}" for name in sorted(values.keys())]
        return [
            ", ".join(items[index : index + items_per_line])
            for index in range(0, len(items), items_per_line)
        ]

    def _comparison_mode_explainer(self) -> str:
        if self.comparison_mode == "direct":
            return "direct; real_image is used only for scoring, not as movie background"
        return "overlay_residual; residual is computed against real_image, not rendered into movie"

    def _board_id_summary(self, limit: int = 12) -> str:
        board_ids = [board.board_id for board in self.boards]
        if len(board_ids) <= limit:
            return ", ".join(board_ids)
        visible = ", ".join(board_ids[:limit])
        return f"{visible}, ... ({len(board_ids)} total)"

    @staticmethod
    def _top_board_summary(total_detail: TotalScoreDetail, limit: int = 3) -> str:
        compared = [score for score in total_detail.board_scores if score.compared]
        if not compared:
            return "worst=none"
        compared.sort(key=lambda score: score.total_score, reverse=True)
        leaders = compared[:limit]
        return "worst=" + ", ".join(
            f"{score.board_id}:{score.total_score:.3f}" for score in leaders
        )

    @staticmethod
    def _compared_board_scores(total_detail: TotalScoreDetail) -> List[BoardScoreDetail]:
        return [score for score in total_detail.board_scores if score.compared]

    def _evaluate_acceptance(self, total_detail: TotalScoreDetail) -> AcceptanceDecision:
        compared_scores = self._compared_board_scores(total_detail)
        compared_board_count = len(compared_scores)
        max_board_score = (
            max(score.total_score for score in compared_scores)
            if compared_scores
            else None
        )
        avg_board_score = (
            sum(score.total_score for score in compared_scores) / compared_board_count
            if compared_scores
            else None
        )
        target_score_reached = total_detail.total_score <= self.target_score

        if not total_detail.success:
            return AcceptanceDecision(
                passed=False,
                mode="failed",
                reason=total_detail.failed_reason or "critical board degraded",
                target_score_reached=target_score_reached,
                compared_board_count=compared_board_count,
                max_board_score=max_board_score,
                avg_board_score=avg_board_score,
            )

        if compared_board_count <= 0:
            return AcceptanceDecision(
                passed=False,
                mode="failed",
                reason="no comparable boards in final result",
                target_score_reached=target_score_reached,
                compared_board_count=compared_board_count,
                max_board_score=max_board_score,
                avg_board_score=avg_board_score,
            )

        if target_score_reached:
            return AcceptanceDecision(
                passed=True,
                mode="target_score",
                reason=(
                    f"best_score={total_detail.total_score:.6f} <= "
                    f"target_score={self.target_score:.6f}"
                ),
                target_score_reached=True,
                compared_board_count=compared_board_count,
                max_board_score=max_board_score,
                avg_board_score=avg_board_score,
            )

        bottleneck_passed = (
            max_board_score is not None
            and avg_board_score is not None
            and max_board_score < self.bottleneck_board_score_max_threshold
            and avg_board_score < self.bottleneck_board_score_avg_threshold
        )
        if bottleneck_passed:
            return AcceptanceDecision(
                passed=True,
                mode="bottleneck_threshold",
                reason=(
                    f"target_score not reached; max_board_score={max_board_score:.6f} < "
                    f"{self.bottleneck_board_score_max_threshold:.6f} and "
                    f"avg_board_score={avg_board_score:.6f} < "
                    f"{self.bottleneck_board_score_avg_threshold:.6f}"
                ),
                target_score_reached=False,
                compared_board_count=compared_board_count,
                max_board_score=max_board_score,
                avg_board_score=avg_board_score,
            )

        max_board_score_text = (
            f"{max_board_score:.6f}" if max_board_score is not None else "none"
        )
        avg_board_score_text = (
            f"{avg_board_score:.6f}" if avg_board_score is not None else "none"
        )
        return AcceptanceDecision(
            passed=False,
            mode="failed",
            reason=(
                "target_score not reached and bottleneck thresholds failed: "
                f"max_board_score={max_board_score_text}, "
                f"avg_board_score={avg_board_score_text}"
            ),
            target_score_reached=False,
            compared_board_count=compared_board_count,
            max_board_score=max_board_score,
            avg_board_score=avg_board_score,
        )

    def _acceptance_payload(self, total_detail: TotalScoreDetail) -> Dict[str, object]:
        decision = self._evaluate_acceptance(total_detail)
        return {
            "passed": decision.passed,
            "mode": decision.mode,
            "reason": decision.reason,
            "target_score": self.target_score,
            "target_score_reached": decision.target_score_reached,
            "bottleneck_board_score_max_threshold": self.bottleneck_board_score_max_threshold,
            "bottleneck_board_score_avg_threshold": self.bottleneck_board_score_avg_threshold,
            "compared_board_count": decision.compared_board_count,
            "max_board_score": decision.max_board_score,
            "avg_board_score": decision.avg_board_score,
            "isolated_outlier_boards": total_detail.isolated_outlier_boards,
        }

    def _print_acceptance_summary(self, total_detail: TotalScoreDetail) -> None:
        acceptance = self._acceptance_payload(total_detail)
        print(
            "Acceptance summary: "
            f"passed={acceptance['passed']} "
            f"mode={acceptance['mode']} "
            f"target_reached={acceptance['target_score_reached']} "
            f"max_board_score={acceptance['max_board_score']} "
            f"avg_board_score={acceptance['avg_board_score']}"
        )
        print("Acceptance reason:", acceptance["reason"])

    def _print_run_summary(self) -> None:
        print(
            f"Run session: id={self.run_session_id}, started_at={self.run_started_at}"
        )
        print(
            "Run summary: "
            f"output_dir={self.output_dir}, "
            f"real_image={self.cfg['real_image']}, "
            f"comparison_mode={self._comparison_mode_explainer()}"
        )
        print(
            "Boards in score: "
            f"{self._board_id_summary()}"
        )
        print(
            "Start values: "
            f"{self._format_value_map(self._snapshot_values())}"
        )
        if self.resume_result_path is not None:
            score_info = (
                f", resumed_best_score={self.resume_best_score:.6f}"
                if self.resume_best_score is not None
                else ""
            )
            print(f"Resume source: {self.resume_result_path}{score_info}")

    def _ensure_live_log(self) -> None:
        if self.live_log_path is not None:
            return
        self.live_log_path = _configure_live_log_for_output_dir(
            self.output_dir,
            self.resume_result_path is not None,
        )
        print("Live log:", str(self.live_log_path))

    def load_best_values_from_result(self, result_path: Path) -> Optional[Dict[str, float]]:
        if not result_path.exists():
            print(f"Resume skipped: result file not found at {result_path}")
            return None

        with open(result_path, "r", encoding="utf-8") as f:
            result = json.load(f)

        result_mode = str(result.get("comparison_mode", "")).lower().strip()
        if result_mode and result_mode != self.comparison_mode:
            print(
                "Resume skipped: comparison_mode mismatch "
                f"({result_mode} != {self.comparison_mode})"
            )
            return None

        result_board_ids = [
            str(entry.get("board_id"))
            for entry in result.get("boards", [])
            if isinstance(entry, dict) and entry.get("board_id") is not None
        ]
        current_board_ids = [board.board_id for board in self.boards]
        if result_board_ids and result_board_ids != current_board_ids:
            print("Resume skipped: board set mismatch between config and result.json")
            return None

        best_values = result.get("best_values")
        if not isinstance(best_values, dict):
            print(f"Resume skipped: best_values missing in {result_path}")
            return None

        applied: Dict[str, float] = {}
        for param in self.params:
            if param.name not in best_values:
                continue
            param.value = self._quantize_param_value(param, float(best_values[param.name]))
            applied[param.name] = param.value

        if not applied:
            print(f"Resume skipped: no matching parameter values found in {result_path}")
            return None

        self.resume_result_path = result_path
        self._restore_counters_from_result(result_path)
        best_score = result.get("best_score")
        self.resume_best_score = float(best_score) if isinstance(best_score, (int, float)) else None
        print(
            "Resuming from existing best result: "
            f"path={result_path}, values={self._format_value_map(applied)}"
        )
        return applied

    def _restore_counters_from_result(self, result_path: Path) -> None:
        if not result_path.exists():
            return
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
            if not isinstance(result, dict):
                print(f"Counter restore skipped: invalid result format in {result_path}")
                return
            self._total_trial_count = int(result.get("total_trial_count", 0))
            self._total_iteration_count = int(result.get("total_iteration_count", 0))
            print(
                f"Restored counters: trial_count={self._total_trial_count}, "
                f"iteration_count={self._total_iteration_count} from {result_path}"
            )
        except Exception as e:
            print(f"Counter restore failed: {e}")

    def _run_script_control_dde_runscript(self, script_path: Path) -> bool:
        self._ensure_dde_dispatch_ready("script_control_runscript")
        try:
            import win32ui  # noqa: F401
            import dde  # type: ignore
        except Exception:
            return False

        server = None
        try:
            server = dde.CreateServer()
            server.Create(f"CopilotScriptControl.{uuid.uuid4().hex}")
            conv = dde.CreateConversation(server)
            conv.ConnectTo(self.script_control_dde_service, self.script_control_dde_topic)
            conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
            return True
        except Exception as exc:
            print(
                "Script Control DDE RunScript failed: "
                f"service={self.script_control_dde_service}, "
                f"topic={self.script_control_dde_topic}, error={exc}"
            )
            return False
        finally:
            if server is not None:
                try:
                    server.Shutdown()
                except Exception:
                    pass

    def _render_script_control_apply_script(self, params: List[ParameterSpec]) -> str:
        unsupported = [p.name for p in params if p.name not in self.SCRIPT_CONTROL_WRITE_WIDGETS]
        if unsupported:
            joined = ", ".join(unsupported)
            raise RuntimeError(f"script_control mode does not support parameters: {joined}")

        body_lines = [
            'if {![winfo exists .camera]} {error "missing widget .camera"}',
        ]

        for param in params:
            widget = self.SCRIPT_CONTROL_WRITE_WIDGETS[param.name]
            value_text = f"{self._quantize_param_value(param, param.value):.{param.decimals}f}"
            body_lines.extend(
                [
                    f'if {{![winfo exists {widget}]}} {{error "missing widget {widget}"}}',
                    f'{widget} delete 0 end',
                    f'{widget} insert 0 {value_text}',
                ]
            )

        body_lines.extend(
            [
                'if {![winfo exists .camera.btn.set]} {error "missing widget .camera.btn.set"}',
                '.camera.btn.set invoke',
                'set result {}',
            ]
        )

        for param in params:
            read_widget = self.SCRIPT_CONTROL_READ_WIDGETS[param.name]
            body_lines.extend(
                [
                    f'if {{![winfo exists {read_widget}]}} {{error "missing widget {read_widget}"}}',
                    f'lappend result "{param.name}=[{read_widget} get]"',
                ]
            )

        body_lines.extend(
            [
                'join $result "\\n"',
            ]
        )
        return render_dde_execute_script(self.script_control_result_path, "IPG-MOVIE", body_lines) + "\n"

    def _render_script_control_read_script(self, params: List[ParameterSpec]) -> str:
        unsupported = [p.name for p in params if p.name not in self.SCRIPT_CONTROL_WRITE_WIDGETS]
        if unsupported:
            joined = ", ".join(unsupported)
            raise RuntimeError(f"script_control mode does not support parameters: {joined}")

        body_lines = [
            'if {![winfo exists .camera]} {error "missing widget .camera"}',
            'set result {}',
        ]

        for param in params:
            widget = self.SCRIPT_CONTROL_WRITE_WIDGETS[param.name]
            body_lines.extend(
                [
                    f'if {{![winfo exists {widget}]}} {{error "missing widget {widget}"}}',
                    f'lappend result "{param.name}=[{widget} get]"',
                ]
            )

        body_lines.extend(
            [
                'join $result "\\n"',
            ]
        )
        return render_dde_execute_script(self.script_control_result_path, "IPG-MOVIE", body_lines) + "\n"

    @staticmethod
    def _parse_script_control_result_text(text: str) -> Tuple[int, str]:
        lines = [line.rstrip("\r") for line in text.splitlines()]
        rc_line = next((line for line in lines if line.startswith("rc=")), None)
        if rc_line is None:
            raise RuntimeError(f"Script Control result is missing rc=: {text!r}")

        try:
            rc = int(rc_line.split("=", 1)[1].strip())
        except ValueError as exc:
            raise RuntimeError(f"Invalid Script Control rc line: {rc_line!r}") from exc

        if "msg_begin" in lines and "msg_end" in lines:
            start = lines.index("msg_begin") + 1
            end = lines.index("msg_end")
            msg = "\n".join(lines[start:end]).strip()
        else:
            msg = "\n".join(line for line in lines if not line.startswith("rc=")).strip()
        return rc, msg

    @staticmethod
    def _is_script_control_result_complete(text: str) -> bool:
        stripped = text.strip()
        if not stripped or "rc=" not in stripped:
            return False
        if "msg_begin" in stripped:
            return "msg_end" in stripped
        return True

    @staticmethod
    def _summarize_dde_detail(detail: object, limit: int = 240) -> str:
        text = str(detail).replace("\r", " ").replace("\n", " | ").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _log_dde_retry_event(
        self,
        operation: str,
        attempt_no: int,
        attempt_count: int,
        status: str,
        elapsed_sec: float,
        detail: Optional[object] = None,
        retry_sleep_sec: Optional[float] = None,
    ) -> None:
        if status == "success" and not self.verbose_dde_diag:
            return
        parts = [
            f"DDE diag [{operation}]",
            f"attempt={attempt_no}/{attempt_count}",
            f"status={status}",
            f"elapsed_sec={elapsed_sec:.3f}",
        ]
        if retry_sleep_sec is not None:
            parts.append(f"retry_sleep_sec={retry_sleep_sec:.3f}")
        if detail is not None:
            detail_text = self._summarize_dde_detail(detail)
            if detail_text:
                parts.append(f"detail={detail_text}")
        print(" ".join(parts))

    def _run_script_control_script(self, script_text: str) -> str:
        self.script_control_script_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_control_result_path.parent.mkdir(parents=True, exist_ok=True)
        result_suffix = self.script_control_result_path.suffix or ".txt"
        invocation_result_path = self.script_control_result_path.with_name(
            f"{self.script_control_result_path.stem}.{uuid.uuid4().hex}{result_suffix}"
        )
        script_text = script_text.replace(
            self.script_control_result_path.as_posix(),
            invocation_result_path.as_posix(),
        )
        self.script_control_script_path.write_text(script_text, encoding="utf-8")
        self._unlink_script_control_result_file(invocation_result_path, required=True)

        last_runtime_error: Optional[RuntimeError] = None
        attempt_count = 6
        retry_delay = max(self.script_control_settle_sec, 0.2)
        for attempt in range(attempt_count):
            attempt_no = attempt + 1
            attempt_started = time.perf_counter()
            attempt_runtime_error: Optional[RuntimeError] = None
            if not self._run_script_control_dde_runscript(self.script_control_script_path):
                attempt_runtime_error = RuntimeError("Script Control DDE RunScript did not execute")
            else:
                deadline = time.time() + self.script_control_timeout_sec
                while time.time() < deadline:
                    if invocation_result_path.exists():
                        text = invocation_result_path.read_text(encoding="utf-8", errors="replace")
                        if self._is_script_control_result_complete(text):
                            rc, msg = self._parse_script_control_result_text(text)
                            if rc != 0:
                                attempt_runtime_error = RuntimeError(
                                    f"Script Control apply failed: {msg}"
                                )
                                self._unlink_script_control_result_file(
                                    invocation_result_path,
                                    required=True,
                                )
                                break
                            self._log_dde_retry_event(
                                "script_control_apply",
                                attempt_no,
                                attempt_count,
                                "success",
                                time.perf_counter() - attempt_started,
                            )
                            self._record_dde_operation_success()
                            self._unlink_script_control_result_file(invocation_result_path)
                            return msg
                    time.sleep(0.1)

            if attempt_runtime_error is None:
                attempt_runtime_error = RuntimeError(
                    "Timed out waiting for Script Control result file"
                )
            last_runtime_error = attempt_runtime_error
            retry_sleep_sec = retry_delay * attempt_no if attempt < attempt_count - 1 else None
            self._log_dde_retry_event(
                "script_control_apply",
                attempt_no,
                attempt_count,
                "retry" if retry_sleep_sec is not None else "failed",
                time.perf_counter() - attempt_started,
                detail=attempt_runtime_error,
                retry_sleep_sec=retry_sleep_sec,
            )
            if retry_sleep_sec is not None:
                if self._runtime_error_needs_dde_recovery_probe(attempt_runtime_error):
                    if self._wait_for_dde_service_recovery():
                        continue
                time.sleep(retry_sleep_sec)

        if last_runtime_error is not None:
            self._record_dde_operation_failure(last_runtime_error, "script_control_apply")
            raise last_runtime_error
        final_error = RuntimeError(
            "Timed out waiting for Script Control result file. "
            f"Script Control did not execute {self.script_control_script_path}."
        )
        self._record_dde_operation_failure(final_error, "script_control_apply")
        raise final_error

    def _unlink_script_control_result_file(
        self,
        result_path: Optional[Path] = None,
        required: bool = False,
    ) -> bool:
        target_path = result_path or self.script_control_result_path
        attempt_count = 10 if required else 5
        retry_base_sec = max(self.script_control_settle_sec, 0.1)
        for attempt in range(attempt_count):
            try:
                target_path.unlink()
                return True
            except FileNotFoundError:
                return True
            except PermissionError as exc:
                if attempt == attempt_count - 1:
                    if required:
                        raise RuntimeError(
                            f"Script Control result file is busy: {target_path}"
                        ) from exc
                    return False
                time.sleep(min(retry_base_sec * (attempt + 1), 1.0))
        return False

    def _runtime_error_needs_dde_recovery_probe(self, exc: BaseException) -> bool:
        text = self._summarize_dde_detail(exc).lower()
        return any(
            marker in text
            for marker in _DDE_RECOVERY_ERROR_MARKERS
        )

    def _reset_dde_dispatch_circuit(self) -> None:
        self.dde_dispatch_failure_streak = 0
        self.dde_circuit_opened_at = None
        self.dde_circuit_last_error_text = ""


    def _ensure_dde_dispatch_ready(self, operation: str) -> None:
        if self._dde_recovery_probe_active or self.dde_circuit_opened_at is None:
            return

        elapsed_sec = max(0.0, time.perf_counter() - self.dde_circuit_opened_at)
        cooldown_remaining = self.dde_circuit_cooldown_sec - elapsed_sec
        if cooldown_remaining > 0:
            self._log_dde_retry_event(
                "dde_dispatch_circuit",
                self.dde_dispatch_failure_streak,
                self.dde_circuit_trip_failures,
                "cooldown",
                elapsed_sec,
                detail=f"operation={operation}",
                retry_sleep_sec=cooldown_remaining,
            )
            time.sleep(cooldown_remaining)

        if self._wait_for_dde_service_recovery():
            self._log_dde_retry_event(
                "dde_dispatch_circuit",
                1,
                1,
                "closed",
                0.0,
                detail=f"operation={operation}",
            )
            return

        detail = self.dde_circuit_last_error_text or "recovery probe failed"
        raise RuntimeError(
            "Timed out waiting for DDE dispatch circuit recovery before "
            f"{operation}; last_error={detail}"
        )

    def _wait_for_dde_service_recovery(self) -> bool:
        prior_probe_state = self._dde_recovery_probe_active
        self._dde_recovery_probe_active = True
        retry_delay = max(self.script_control_settle_sec, 0.5)
        attempt_count = 4
        try:
            for attempt in range(attempt_count):
                attempt_no = attempt + 1
                attempt_started = time.perf_counter()
                try:
                    self._get_movie_dde_view_size(allow_cached_fallback=False)
                    self._log_dde_retry_event(
                        "dde_recovery_probe",
                        attempt_no,
                        attempt_count,
                        "success",
                        time.perf_counter() - attempt_started,
                    )
                    self._reset_dde_dispatch_circuit()
                    return True
                except Exception as exc:
                    retry_sleep_sec = retry_delay * attempt_no if attempt < attempt_count - 1 else None
                    self._log_dde_retry_event(
                        "dde_recovery_probe",
                        attempt_no,
                        attempt_count,
                        "retry" if retry_sleep_sec is not None else "failed",
                        time.perf_counter() - attempt_started,
                        detail=exc,
                        retry_sleep_sec=retry_sleep_sec,
                    )
                    if retry_sleep_sec is not None:
                        time.sleep(retry_sleep_sec)
            self._log_dde_retry_event(
                "movie_restart_recovery",
                self.gui_movie_restart_recovery_attempts,
                self.max_gui_movie_restart_recoveries,
                "disabled",
                0.0,
                detail="GUI Movie auto-restart disabled for DDE failures; leaving current failure to bubble up",
            )
            return False
        finally:
            self._dde_recovery_probe_active = prior_probe_state

    @staticmethod
    def _extract_cli_arg_value(command_line: str, option_name: str) -> Optional[str]:
        match = re.search(
            rf"(?:^|\s){re.escape(option_name)}\s+(\"[^\"]*\"|\S+)",
            command_line,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        return match.group(1).strip().strip('"')

    @staticmethod
    def _replace_cli_arg_value(command_line: str, option_name: str, new_value: str) -> str:
        replacement = f"{option_name} {new_value}"
        pattern = re.compile(
            rf"((?:^|\s){re.escape(option_name)}\s+)(\"[^\"]*\"|\S+)",
            flags=re.IGNORECASE,
        )
        if pattern.search(command_line):
            return pattern.sub(lambda match: f"{match.group(1)}{new_value}", command_line, count=1)
        return f"{command_line} {replacement}".strip()

    def _build_gui_movie_relaunch_command(
        self,
        existing_gui_movies: List[dict],
        carmaker_pid: int,
    ) -> str:
        for process in existing_gui_movies:
            command_line = str(process.get("CommandLine") or "").strip()
            if not command_line:
                continue
            if "-cmgui" not in command_line.lower():
                continue
            existing_apppid = self._extract_cli_arg_value(command_line, "-apppid")
            if existing_apppid and existing_apppid.lower() != "none":
                return self._replace_cli_arg_value(command_line, "-apppid", str(carmaker_pid))

        movie_executable = (self.cm_install_root / "GUI" / "Movie.exe").resolve()
        project_dir = self.repo_root.resolve().as_posix()
        datapool_dir = self.cm_install_root.resolve().as_posix()
        return (
            f'"{movie_executable}" -CMInstance 0 '
            f"-apphost {self.movie_apphost} "
            f"-apppid {carmaker_pid} "
            f"-projectdir {project_dir} "
            f"-datapool {datapool_dir} "
            "-cmgui CarMaker"
        )

    def _restart_gui_movie_for_dde_recovery(self) -> bool:
        attempt_started = time.perf_counter()
        if self.gui_movie_restart_recovery_attempts >= self.max_gui_movie_restart_recoveries:
            self._log_dde_retry_event(
                "movie_restart_recovery",
                self.gui_movie_restart_recovery_attempts,
                self.max_gui_movie_restart_recoveries,
                "skipped",
                0.0,
                detail="restart fuse open before GUI Movie relaunch",
            )
            return False
        self.gui_movie_restart_recovery_attempts += 1
        attempt_no = self.gui_movie_restart_recovery_attempts
        attempt_limit = max(self.max_gui_movie_restart_recoveries, 1)
        try:
            from src.cmapi import cmapi_testrun_control as cmctrl

            existing_carmakers = cmctrl.list_carmaker_processes()
            runtime_carmakers = [
                proc for proc in existing_carmakers if str(proc.get("Name") or "") == "CarMaker.win64.exe"
            ]
            if len(runtime_carmakers) == 1:
                selected_process = runtime_carmakers[0]
            elif len(existing_carmakers) == 1:
                selected_process = existing_carmakers[0]
            else:
                summary = ", ".join(
                    f"{proc.get('Name')}[{proc.get('ProcessId')}]" for proc in existing_carmakers
                ) or "none"
                raise RuntimeError(
                    "expected a single attachable CarMaker runtime process for Movie restart recovery, "
                    f"found: {summary}"
                )

            carmaker_pid = int(selected_process["ProcessId"])
            existing_gui_movies = cmctrl.list_gui_movie_processes()
            relaunch_command = self._build_gui_movie_relaunch_command(
                existing_gui_movies,
                carmaker_pid,
            )
            killed_gui_movies = cmctrl.kill_gui_movie_processes()
            subprocess.Popen(
                relaunch_command,
                cwd=str(self.repo_root),
            )
            time.sleep(max(self.movie_restart_settle_sec, self.script_control_settle_sec, 0.5))
            width, height = self._get_movie_dde_view_size(allow_cached_fallback=False)
            killed_pids = ",".join(str(proc["ProcessId"]) for proc in killed_gui_movies) or "-"
            self._log_dde_retry_event(
                "movie_restart_recovery",
                attempt_no,
                attempt_limit,
                "success",
                time.perf_counter() - attempt_started,
                detail=(
                    f"carmaker_pid={carmaker_pid} killed_gui_movie_pids={killed_pids} "
                    f"relaunch={relaunch_command} size={width}x{height}"
                ),
            )
            return True
        except Exception as exc:
            self._log_dde_retry_event(
                "movie_restart_recovery",
                attempt_no,
                attempt_limit,
                "failed",
                time.perf_counter() - attempt_started,
                detail=exc,
            )
            return False

    def _recover_after_runtime_error(
        self,
        expected_values: Dict[str, float],
        cause: Optional[BaseException] = None,
    ) -> bool:
        for param in self.params:
            if param.name in expected_values:
                param.value = self._quantize_param_value(param, float(expected_values[param.name]))

        if cause is not None and self._runtime_error_needs_dde_recovery_probe(cause):
            if not self._wait_for_dde_service_recovery():
                return False

        retry_delay = max(self.script_control_settle_sec, 0.2)
        for attempt in range(4):
            try:
                self._apply_value_map(expected_values)
                return True
            except RuntimeError:
                if attempt == 3:
                    return False
                time.sleep(retry_delay * (attempt + 1))

        return False


    def _apply_initial_value_map_with_retry(self, values: Dict[str, float], context: str) -> None:
        retry_delay = max(self.script_control_settle_sec, 0.2)
        last_error: Optional[RuntimeError] = None
        for attempt in range(5):
            try:
                self._apply_value_map(values)
                return
            except RuntimeError as exc:
                last_error = exc
                if attempt == 4:
                    break
                time.sleep(retry_delay * (attempt + 1))

        if last_error is not None:
            raise RuntimeError(f"{context}: {last_error}") from last_error
        raise RuntimeError(context)

    def _read_script_control_values(self, params: List[ParameterSpec]) -> Dict[str, float]:
        msg = self._run_script_control_script(self._render_script_control_read_script(params))
        captured: Dict[str, float] = {}
        for line in msg.splitlines():
            if "=" not in line:
                continue
            name, raw_value = line.split("=", 1)
            try:
                captured[name.strip()] = float(raw_value.replace(",", ".").strip())
            except ValueError:
                continue
        return captured


    def capture_initial_values(self) -> Dict[str, float]:
        captured = self._read_script_control_values(self.params)
        print("Reading current values through Script Control...")
        for param in self.params:
            if param.name not in captured:
                raise RuntimeError(f"Script Control did not return a value for {param.name}")
            value = captured[param.name]
            captured[param.name] = value
            print(f"{param.name}: {value}")
        return captured

    @staticmethod
    def _quantize_value(value: float, decimals: int) -> float:
        return float(f"{float(value):.{decimals}f}")

    def _script_control_readback_matches(
        self,
        expected: float,
        actual: Optional[float],
        spec_decimals: int,
        read_decimals: int,
    ) -> bool:
        if actual is None:
            return False

        expected_readback = self._quantize_value(expected, read_decimals)
        read_unit = 10 ** (-read_decimals)
        # Script Control position fields read back at 3 decimals and can land one
        # displayed step away from the written value because of truncation/rounding.
        tolerance = read_unit if read_decimals < spec_decimals else read_unit * 0.5
        tolerance += 1e-12
        return math.isclose(
            actual,
            expected_readback,
            rel_tol=0.0,
            abs_tol=max(tolerance, 1e-6),
        )

    def _quantize_param_value(self, spec: ParameterSpec, value: float) -> float:
        clipped = float(np.clip(value, spec.min_value, spec.max_value))
        return self._quantize_value(clipped, spec.decimals)

    def _apply_single_param(self, p: ParameterSpec, verify_all: bool = False) -> None:
        p.value = self._quantize_param_value(p, p.value)
        self._apply_script_control_params([p])

    def apply_params(self, params: List[ParameterSpec]) -> None:
        self._apply_script_control_params(params)

    def _get_movie_dde_view_size(self, allow_cached_fallback: bool = True) -> Tuple[int, int]:
        try:
            import win32ui  # noqa: F401
            import dde  # type: ignore
        except Exception as exc:
            raise RuntimeError("movie size probe requires pywin32 DDE support") from exc

        last_runtime_error: Optional[RuntimeError] = None
        attempt_count = 6
        retry_delay = max(self.script_control_settle_sec, 0.2)
        for attempt in range(attempt_count):
            attempt_no = attempt + 1
            attempt_started = time.perf_counter()
            attempt_runtime_error: Optional[RuntimeError] = None

            invocation_id = uuid.uuid4().hex
            script_path = self.output_dir / f"movie_size_probe_dde.{invocation_id}.tcl"
            result_path = self.output_dir / f"movie_size_probe_dde.{invocation_id}.txt"
            script_text = render_dde_execute_script(
                result_path,
                "IPG-MOVIE",
                [
                    "scan $View(ev.view) %d vno",
                    "set wpath .view$vno",
                    "set wi [$wpath.gl0 cget -width]",
                    "set he [$wpath.gl0 cget -height]",
                    "list $wi $he",
                ],
            )
            script_path.write_text(script_text, encoding="utf-8")
            _unlink_if_exists(result_path)
            self._ensure_dde_dispatch_ready("movie_size_probe")

            server = None
            try:
                server = dde.CreateServer()
                server.Create(f"CopilotMovieSizeProbe.{uuid.uuid4().hex}")
                conv = dde.CreateConversation(server)
                conv.ConnectTo(self.script_control_dde_service, self.script_control_dde_topic)
                conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
            except Exception as exc:
                attempt_runtime_error = RuntimeError(f"movie size probe RunScript failed: {exc}")
            finally:
                if server is not None:
                    try:
                        server.Shutdown()
                    except Exception:
                        pass

            if attempt_runtime_error is None:
                deadline = time.time() + self.script_control_timeout_sec
                while time.time() < deadline:
                    if result_path.exists():
                        text = result_path.read_text(encoding="utf-8", errors="replace")
                        if self._is_script_control_result_complete(text):
                            rc, msg = self._parse_script_control_result_text(text)
                            if rc != 0:
                                attempt_runtime_error = RuntimeError(f"movie size probe failed: {msg}")
                                try:
                                    result_path.unlink()
                                except FileNotFoundError:
                                    pass
                                break
                            parts = str(msg).split()
                            if len(parts) != 2:
                                raise RuntimeError(f"movie size probe returned unexpected payload: {msg}")
                            width = int(parts[0])
                            height = int(parts[1])
                            if width <= 0 or height <= 0:
                                raise RuntimeError(f"movie size probe returned invalid size: {width}x{height}")
                            self.movie_size_cache_path.parent.mkdir(parents=True, exist_ok=True)
                            self.movie_size_cache_path.write_text(f"{width} {height}\n", encoding="utf-8")
                            self._log_dde_retry_event(
                                "movie_size_probe",
                                attempt_no,
                                attempt_count,
                                "success",
                                time.perf_counter() - attempt_started,
                                detail=f"size={width}x{height}",
                            )
                            self._record_dde_operation_success()
                            _unlink_if_exists(script_path)
                            _unlink_if_exists(result_path)
                            return width, height
                    time.sleep(0.05)

            if attempt_runtime_error is None:
                attempt_runtime_error = RuntimeError("Timed out waiting for movie size probe result")
            last_runtime_error = attempt_runtime_error
            retry_sleep_sec = retry_delay * attempt_no if attempt < attempt_count - 1 else None
            self._log_dde_retry_event(
                "movie_size_probe",
                attempt_no,
                attempt_count,
                "retry" if retry_sleep_sec is not None else "failed",
                time.perf_counter() - attempt_started,
                detail=attempt_runtime_error,
                retry_sleep_sec=retry_sleep_sec,
            )
            _unlink_if_exists(script_path)
            _unlink_if_exists(result_path)
            if retry_sleep_sec is not None:
                if self._runtime_error_needs_dde_recovery_probe(attempt_runtime_error):
                    if self._wait_for_dde_service_recovery():
                        continue
                time.sleep(retry_sleep_sec)

        if allow_cached_fallback and self.movie_size_cache_path.exists():
            cached_text = self.movie_size_cache_path.read_text(encoding="utf-8", errors="replace").strip()
            parts = cached_text.split()
            if len(parts) == 2:
                try:
                    width = int(parts[0])
                    height = int(parts[1])
                except ValueError:
                    width = 0
                    height = 0
                if width > 0 and height > 0:
                    print(
                        "Movie size probe fallback: "
                        f"using cached size {width}x{height} from {self.movie_size_cache_path}"
                    )
                    return width, height

        if last_runtime_error is not None:
            self._record_dde_operation_failure(last_runtime_error, "movie_size_probe")
            raise last_runtime_error
        final_error = RuntimeError("Timed out waiting for movie size probe result")
        self._record_dde_operation_failure(final_error, "movie_size_probe")
        raise final_error






    def _capture_carmaker_error_dialog(self) -> Optional[str]:
        """Capture CarMaker/IPG-MOVIE error dialog content via clipboard (Ctrl+A + Ctrl+C).
        Returns the error text if found, None otherwise.
        Use when DDE is down and standard diag probes fail.
        """
        try:
            import ctypes
            from ctypes import wintypes
            import time

            user32 = ctypes.windll.user32

            # Find IPGMovie Internal Debugger window
            found = []
            def enum_proc(hwnd, lparam):
                length = user32.GetWindowTextLengthW(hwnd) + 1
                buffer = ctypes.create_unicode_buffer(length)
                user32.GetWindowTextW(hwnd, buffer, length)
                title = buffer.value
                if "Internal Debugger" in title or ("IPGMovie" in title and "Debugger" in title):
                    found.append(hwnd)
                return True
            callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(enum_proc)
            user32.EnumWindows(callback, 0)

            if not found:
                return None

            hwnd = found[0]
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)

            # Ctrl+A (select all)
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x41, 0, 0, 0)
            time.sleep(0.05)
            ctypes.windll.user32.keybd_event(0x41, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.1)

            # Ctrl+C (copy)
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x43, 0, 0, 0)
            time.sleep(0.05)
            ctypes.windll.user32.keybd_event(0x43, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.2)

            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                data = win32clipboard.GetClipboardData()
                return data.strip() if data.strip() else None
            except Exception:
                return None
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return None



    @staticmethod
    def _annotated_label_bounds(
        image_shape: Tuple[int, ...],
        text: str,
        anchor: Tuple[int, int],
        scale: float = 0.62,
        thickness: int = 2,
    ) -> Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]:
        font = cv2.FONT_HERSHEY_SIMPLEX
        (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
        image_h, image_w = image_shape[:2]
        x = max(8, int(anchor[0]))
        x = min(x, max(8, image_w - text_w - 8))
        y = max(text_h + 12, int(anchor[1]))
        y = min(y, max(text_h + 12, image_h - baseline + 2))
        top_left = (x - 4, y - text_h - 8)
        bottom_right = (x + text_w + 6, y + baseline - 2)
        return top_left, bottom_right, (x, y)


    def propose_boards_config(
        self,
        config_path: str,
        output_path: Optional[str] = None,
        preview_path: Optional[str] = None,
    ) -> Tuple[Path, Path, List[dict]]:
        config_file = Path(config_path)
        with open(config_file, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)

        proposal_file = Path(output_path) if output_path else config_file.with_name(
            f"{config_file.stem}.proposed{config_file.suffix}"
        )
        resolved_output_dir = _resolve_config_output_dir(cfg, config_file)
        preview_file = Path(preview_path) if preview_path else resolved_output_dir / "board_proposal_preview.png"
        proposal_file.parent.mkdir(parents=True, exist_ok=True)
        preview_file.parent.mkdir(parents=True, exist_ok=True)

        proposed_boards: List[dict] = []
        preview_items: List[dict] = []
        prepared_real = self._prepare_eval_image(self.real_img)

        checkerboard_groups: Dict[Tuple[int, int], List[dict]] = {}
        remaining_prototypes: List[dict] = []
        for prototype_cfg in cfg.get("boards", []):
            board_type = str(prototype_cfg.get("board_type", "")).strip().lower()
            if board_type == "checkerboard":
                raw_size = prototype_cfg.get("board_size")
                if isinstance(raw_size, list) and len(raw_size) == 2:
                    key = (int(raw_size[0]), int(raw_size[1]))
                    checkerboard_groups.setdefault(key, []).append(prototype_cfg)
                    continue
            remaining_prototypes.append(prototype_cfg)

        for board_size, family_cfgs in checkerboard_groups.items():
            family_board_id = str(family_cfgs[0].get("board_id", "checkerboard")).strip() or "checkerboard"
            prototype = next(
                (
                    board
                    for board in self.boards
                    if board.board_type == "checkerboard" and board.board_size == board_size
                ),
                None,
            )
            if prototype is None:
                continue

            max_instances = max(
                int(item.get("proposal_max_instances", 4)) for item in family_cfgs
            )
            candidate_entries = self._detect_checkerboard_instances(
                prepared_real, prototype, max_instances=max_instances
            )
            if not candidate_entries:
                fallback = self._detect_board(prepared_real, prototype)
                if fallback.success and fallback.ordered_points.size > 0:
                    bbox = self._expand_bbox(
                        self._points_bbox(fallback.ordered_points), prepared_real.shape[:2]
                    )
                    candidate_entries = [(fallback, bbox)]

            generic_prefix = (
                family_board_id if len(family_cfgs) == 1 else f"checkerboard_{board_size[0]}x{board_size[1]}"
            )
            prototype_cfg = dict(family_cfgs[0])
            for index, (detection, bbox) in enumerate(candidate_entries, start=1):
                candidate_cfg = dict(prototype_cfg)
                candidate_cfg["board_id"] = (
                    f"{generic_prefix}_{index:02d}" if len(candidate_entries) > 1 or len(family_cfgs) > 1 else generic_prefix
                )
                candidate_cfg["roi"] = [int(v) for v in bbox]
                proposed_boards.append(candidate_cfg)
                preview_items.append(
                    {
                        "board_id": str(candidate_cfg["board_id"]),
                        "board_type": prototype.board_type,
                        "bbox": bbox,
                        "points": detection.ordered_points.copy(),
                    }
                )

        for prototype_cfg in remaining_prototypes:
            board_id = str(prototype_cfg.get("board_id", "")).strip()
            prototype = next((board for board in self.boards if board.board_id == board_id), None)
            if prototype is None:
                continue

            default_instances = 1 if _is_custom_marker_board_type(prototype.board_type) else 4
            max_instances = int(prototype_cfg.get("proposal_max_instances", default_instances))
            candidate_entries: List[Tuple[DetectionResult, Tuple[int, int, int, int]]] = []

            if prototype.template_image:
                candidate_entries = self._detect_template_instances(
                    prepared_real, prototype, max_instances=max_instances
                )
            else:
                single = self._detect_board(prepared_real, prototype)
                if single.success and single.ordered_points.size > 0:
                    bbox = self._expand_bbox(
                        self._points_bbox(single.ordered_points), prepared_real.shape[:2]
                    )
                    candidate_entries = [(single, bbox)]

            for index, (detection, bbox) in enumerate(candidate_entries, start=1):
                candidate_cfg = dict(prototype_cfg)
                candidate_cfg["board_id"] = f"{board_id}_{index:02d}" if max_instances > 1 else board_id
                candidate_cfg["roi"] = [int(v) for v in bbox]
                proposed_boards.append(candidate_cfg)
                preview_items.append(
                    {
                        "board_id": str(candidate_cfg["board_id"]),
                        "board_type": prototype.board_type,
                        "bbox": bbox,
                        "points": detection.ordered_points.copy(),
                    }
                )

        cfg["boards"] = proposed_boards
        with open(proposal_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)

        preview_image = cv2.cvtColor(prepared_real, cv2.COLOR_GRAY2BGR)
        palette = [
            (70, 80, 230),
            (60, 170, 90),
            (220, 110, 60),
            (180, 60, 180),
            (70, 170, 200),
            (200, 200, 70),
        ]
        for index, item in enumerate(preview_items, start=1):
            color = palette[(index - 1) % len(palette)]
            x, y, width, height = item["bbox"]
            cv2.rectangle(preview_image, (x, y), (x + width, y + height), color, 3)
            label = f"{index}:{item['board_id']}"
            cv2.putText(
                preview_image,
                label,
                (x, max(24, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                color,
                2,
                cv2.LINE_AA,
            )
            for point in item["points"].reshape(-1, 2):
                cv2.circle(
                    preview_image,
                    (int(round(float(point[0]))), int(round(float(point[1])))),
                    4,
                    color,
                    -1,
                )

        cv2.imwrite(str(preview_file), preview_image)
        print(f"Proposed boards config: {proposal_file}")
        print(f"Proposal preview image: {preview_file}")
        for item in preview_items:
            x, y, width, height = item["bbox"]
            print(
                f"{item['board_id']}: type={item['board_type']} roi=[{x}, {y}, {width}, {height}]"
            )
        return proposal_file, preview_file, proposed_boards

    def _as_baseline_metrics(
        self, total_detail: TotalScoreDetail
    ) -> Dict[str, Dict[str, float]]:
        baseline: Dict[str, Dict[str, float]] = {}
        for score in total_detail.board_scores:
            if not score.compared:
                continue
            baseline[score.board_id] = {
                "total_score": score.total_score,
                "rmse": score.rmse,
                "max_error": score.max_error,
                "miss_rate": score.miss_rate,
            }
        return baseline

    def _isolated_outlier_board_ids(
        self,
        board_scores: List[BoardScoreDetail],
        baseline_metrics: Optional[Dict[str, Dict[str, float]]],
    ) -> List[str]:
        if not self.isolated_board_guard_enabled or not baseline_metrics:
            return []

        compared_scores = [score for score in board_scores if score.compared]
        if len(compared_scores) < self.isolated_board_guard_min_other_boards + 1:
            return []

        candidates: List[Tuple[float, str]] = []
        for score in compared_scores:
            baseline = baseline_metrics.get(score.board_id)
            if not isinstance(baseline, dict):
                continue
            try:
                baseline_total_score = float(baseline.get("total_score", score.total_score))
            except Exception:
                baseline_total_score = float(score.total_score)
            if baseline_total_score <= 0.0:
                baseline_total_score = max(1e-6, float(score.total_score))

            peer_scores = [
                float(peer.total_score)
                for peer in compared_scores
                if peer.board_id != score.board_id
            ]
            if len(peer_scores) < self.isolated_board_guard_min_other_boards:
                continue
            peer_median_score = float(np.median(np.asarray(peer_scores, dtype=np.float64)))
            if peer_median_score <= 0.0:
                continue

            current_score = float(score.total_score)
            if current_score < self.isolated_board_guard_abs_score_threshold:
                continue
            if current_score < baseline_total_score * self.isolated_board_guard_baseline_ratio:
                continue
            if current_score < peer_median_score * self.isolated_board_guard_peer_ratio:
                continue
            candidates.append((current_score, score.board_id))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return [
            board_id
            for _, board_id in candidates[: self.isolated_board_guard_max_boards]
        ]

    def _objective_focus_multiplier_map(
        self,
        board_scores: List[BoardScoreDetail],
        isolated_outlier_board_set: set[str],
    ) -> Dict[str, float]:
        if not self.objective_board_focus_enabled:
            return {}

        candidates = [
            score
            for score in board_scores
            if score.compared
            and score.board_id not in isolated_outlier_board_set
            and float(score.total_score) >= self.objective_board_focus_score_threshold
        ]
        if self.objective_board_focus_board_ids:
            candidates = [
                score
                for score in candidates
                if score.board_id in self.objective_board_focus_board_ids
            ]
        if not candidates:
            return {}

        board_type_multipliers = self.objective_board_focus_type_multipliers
        rank_multipliers = self.objective_board_focus_rank_multipliers
        focus_map: Dict[str, float] = {}
        candidates.sort(key=lambda score: float(score.total_score), reverse=True)
        for index, score in enumerate(candidates[: self.objective_board_focus_top_k]):
            rank_multiplier = rank_multipliers[min(index, len(rank_multipliers) - 1)]
            board_type_multiplier = board_type_multipliers.get(
                str(score.board_type).strip().lower(),
                1.0,
            )
            multiplier = rank_multiplier * board_type_multiplier
            if score.board_id in self.priority_board_accept_ids:
                multiplier *= self.objective_board_focus_priority_board_multiplier
            focus_map[score.board_id] = max(1.0, float(multiplier))
        return focus_map

    def _priority_board_improvements(
        self,
        baseline_detail: TotalScoreDetail,
        candidate_detail: TotalScoreDetail,
    ) -> List[Tuple[str, float]]:
        if not self.priority_board_accept_ids:
            return []

        baseline_scores = {
            score.board_id: score
            for score in baseline_detail.board_scores
            if score.compared and score.success
        }
        candidate_scores = {
            score.board_id: score
            for score in candidate_detail.board_scores
            if score.compared and score.success
        }

        improvements: List[Tuple[str, float]] = []
        for board_id in self.priority_board_accept_ids:
            baseline_score = baseline_scores.get(board_id)
            candidate_score = candidate_scores.get(board_id)
            if baseline_score is None or candidate_score is None:
                continue
            improvement = baseline_score.total_score - candidate_score.total_score
            if improvement >= self.priority_board_accept_min_improvement:
                improvements.append((board_id, improvement))
        return improvements

    @staticmethod
    def _priority_board_total_improvement(improvements: List[Tuple[str, float]]) -> float:
        return float(sum(improvement for _, improvement in improvements))

    def _acceptance_decision(
        self,
        baseline_score: float,
        baseline_detail: TotalScoreDetail,
        candidate_score: float,
        candidate_detail: TotalScoreDetail,
    ) -> Tuple[bool, str]:
        if candidate_detail.compared_board_count <= 0:
            return False, "no_comparable_boards"
        if candidate_detail.has_critical_degrade:
            return False, "critical_degrade"
        if candidate_score + self.min_improve < baseline_score:
            return True, "total_score_improved"

        if (
            not self.priority_board_accept_ids
            or self.priority_board_accept_min_improvement <= 0.0
            or self.priority_board_accept_max_total_worsen <= 0.0
        ):
            return False, "total_score_not_improved"

        score_worsen = candidate_score - baseline_score
        if score_worsen > self.priority_board_accept_max_total_worsen:
            return False, "priority_worsen_limit_exceeded"

        improvements = self._priority_board_improvements(baseline_detail, candidate_detail)
        if len(improvements) < self.priority_board_accept_min_count:
            return False, "priority_board_improvement_insufficient"

        total_priority_improvement = self._priority_board_total_improvement(improvements)
        if total_priority_improvement < self.priority_board_accept_min_total_improvement:
            return False, "priority_total_improvement_insufficient"
        if (
            score_worsen > 0.0
            and self.priority_board_accept_worsen_tradeoff_ratio > 0.0
            and total_priority_improvement
            < score_worsen * self.priority_board_accept_worsen_tradeoff_ratio
        ):
            return False, "priority_tradeoff_ratio_insufficient"

        improvement_summary = ",".join(
            f"{board_id}:{improvement:.3f}" for board_id, improvement in improvements
        )
        return True, (
            "priority_board_override["
            f"total={total_priority_improvement:.3f};{improvement_summary}]"
        )

    def _is_joint_exploration_param(self, param_name: str) -> bool:
        return (
            param_name in self.joint_exploration_param_set
            and self.joint_exploration_max_single_worsen > 0.0
        )


    @staticmethod
    def _normalize_trial_multiplier_values(
        raw_values: object,
        default: List[float],
    ) -> List[float]:
        values = raw_values if isinstance(raw_values, list) else default
        normalized: List[float] = []
        for raw_value in values:
            try:
                multiplier = abs(float(raw_value))
            except (TypeError, ValueError):
                continue
            if multiplier <= 0.0:
                continue
            if any(math.isclose(multiplier, seen, rel_tol=0.0, abs_tol=1e-12) for seen in normalized):
                continue
            normalized.append(multiplier)
        return normalized or list(default)

    @staticmethod
    def _merge_trial_multiplier_sequences(*sequences: List[float]) -> List[float]:
        merged: List[float] = []
        for sequence in sequences:
            for raw_value in sequence:
                try:
                    multiplier = abs(float(raw_value))
                except (TypeError, ValueError):
                    continue
                if multiplier <= 0.0:
                    continue
                if any(math.isclose(multiplier, seen, rel_tol=0.0, abs_tol=1e-12) for seen in merged):
                    continue
                merged.append(multiplier)
        return merged or [1.0]





    def _ordered_params_for_iteration(self) -> List[ParameterSpec]:
        if not (self.strategy_adaptation_enabled and self.strategy_reorder_params):
            return list(self.params)
        params = sorted(
            self.params,
            key=lambda param: (
                -float(self.strategy_param_state.get(param.name, {}).get("priority_score", 0.0)),
                self.param_order_index.get(param.name, len(self.param_order_index)),
            ),
        )
        if (hasattr(self, 'curriculum_enabled') and self.curriculum_enabled
                and len(self.params) > 6):
            progress = self._total_iteration_count / max(1, self.max_iters)
            for phase in self.curriculum_phases:
                if progress <= phase.get("progress_max", 1.0):
                    active = phase.get("active_params")
                    if active is not None:
                        params = [p for p in params if p.name in active]
                    break
        return params


    def _new_strategy_iteration_stats(self) -> Dict[str, Dict[str, object]]:
        if not self.strategy_adaptation_enabled:
            return {}
        return {
            param.name: {
                "attempts": 0,
                "accepted_count": 0,
                "joint_candidate_count": 0,
                "best_score_delta": None,
                "accepted_score_delta": None,
                "accepted_trial_multiplier": 1.0,
                "bottleneck_boost": 0.0,
                "bottleneck_boards": [],
            }
            for param in self.params
        }

    def _record_strategy_trial(
        self,
        iteration_stats: Dict[str, Dict[str, object]],
        *,
        param_name: str,
        accepted: bool,
        joint_candidate: bool,
        score_delta: Optional[float],
        trial_multiplier: float,
        baseline_detail: Optional[TotalScoreDetail] = None,
        candidate_detail: Optional[TotalScoreDetail] = None,
    ) -> None:
        if not self.strategy_adaptation_enabled:
            return
        stats = iteration_stats.setdefault(
            param_name,
            {
                "attempts": 0,
                "accepted_count": 0,
                "joint_candidate_count": 0,
                "best_score_delta": None,
                "accepted_score_delta": None,
                "accepted_trial_multiplier": 1.0,
                "bottleneck_boost": 0.0,
                "bottleneck_boards": [],
            },
        )
        stats["attempts"] = int(stats.get("attempts", 0)) + 1
        bottleneck_boost, improved_bottleneck_boards = self._strategy_bottleneck_focus_boost(
            baseline_detail,
            candidate_detail,
        )
        if bottleneck_boost > 0.0:
            stats["bottleneck_boost"] = float(stats.get("bottleneck_boost", 0.0)) + bottleneck_boost
            seen_boards = [str(board_id) for board_id in stats.get("bottleneck_boards", [])]
            for board_id in improved_bottleneck_boards:
                if board_id not in seen_boards:
                    seen_boards.append(board_id)
            stats["bottleneck_boards"] = seen_boards
        if score_delta is not None:
            best_score_delta = stats.get("best_score_delta")
            if best_score_delta is None or score_delta < float(best_score_delta):
                stats["best_score_delta"] = score_delta
        if accepted:
            stats["accepted_count"] = int(stats.get("accepted_count", 0)) + 1
            accepted_score_delta = stats.get("accepted_score_delta")
            if accepted_score_delta is None or (
                score_delta is not None and score_delta < float(accepted_score_delta)
            ):
                stats["accepted_score_delta"] = score_delta
            stats["accepted_trial_multiplier"] = max(
                float(stats.get("accepted_trial_multiplier", 1.0)),
                float(trial_multiplier),
            )
            return
        if joint_candidate:
            stats["joint_candidate_count"] = int(stats.get("joint_candidate_count", 0)) + 1

    def _finalize_strategy_iteration(
        self,
        iteration: int,
        iteration_stats: Dict[str, Dict[str, object]],
        *,
        improved_in_iter: bool,
    ) -> None:
        if not self.strategy_adaptation_enabled:
            return

        if improved_in_iter:
            self.strategy_stagnation_count = 0
        else:
            self.strategy_stagnation_count += 1

        for param in self.params:
            state = self.strategy_param_state[param.name]
            stats = iteration_stats.get(param.name, {})
            attempts = int(stats.get("attempts", 0))
            accepted_count = int(stats.get("accepted_count", 0))
            joint_candidate_count = int(stats.get("joint_candidate_count", 0))
            bottleneck_boost = float(stats.get("bottleneck_boost", 0.0))
            bottleneck_boards = [
                str(board_id) for board_id in stats.get("bottleneck_boards", [])
            ]

            state["priority_score"] = float(state.get("priority_score", 0.0)) * self.strategy_priority_decay
            state["bottleneck_focus_score"] = float(
                state.get("bottleneck_focus_score", 0.0)
            ) * self.strategy_priority_decay
            state["attempt_count"] = int(state.get("attempt_count", 0)) + attempts
            state["accepted_count"] = int(state.get("accepted_count", 0)) + accepted_count
            state["joint_candidate_count"] = int(state.get("joint_candidate_count", 0)) + joint_candidate_count
            if bottleneck_boards:
                state["last_bottleneck_boards"] = bottleneck_boards

            if bottleneck_boost > 0.0:
                state["bottleneck_focus_score"] = float(state["bottleneck_focus_score"]) + bottleneck_boost
                state["priority_score"] = float(state["priority_score"]) + (
                    self.strategy_bottleneck_priority_boost * bottleneck_boost
                )

            if accepted_count > 0:
                accepted_score_delta = stats.get("accepted_score_delta")
                accepted_delta = (
                    float(accepted_score_delta)
                    if accepted_score_delta is not None
                    else 0.0
                )
                accepted_multiplier = float(stats.get("accepted_trial_multiplier", 1.0))
                boost = self.strategy_accepted_priority_boost + min(
                    2.0,
                    max(0.0, -accepted_delta),
                )
                state["priority_score"] = float(state["priority_score"]) + boost
                state["last_accepted_iteration"] = iteration
                if self.strategy_adjust_step_scale:
                    step_scale = float(state.get("step_scale", 1.0))
                    if accepted_multiplier > 1.0 or accepted_delta < -(self.min_improve * 10.0):
                        step_scale *= self.strategy_step_scale_up
                    else:
                        step_scale *= self.strategy_step_scale_down
                    state["step_scale"] = self._clamp_strategy_step_scale(step_scale)
                continue

            if attempts > 0:
                state["priority_score"] = float(state["priority_score"]) - self.strategy_rejected_priority_penalty

            if joint_candidate_count > 0 and self.strategy_focus_on_joint_candidates:
                state["priority_score"] = float(state["priority_score"]) + (
                    self.strategy_joint_candidate_priority_boost * joint_candidate_count
                )

            if (
                self.strategy_adjust_step_scale
                and attempts > 0
                and self.strategy_stagnation_count >= self.strategy_stagnation_patience
            ):
                state["step_scale"] = self._clamp_strategy_step_scale(
                    float(state.get("step_scale", 1.0)) * self.strategy_stagnation_step_scale_up
                )






