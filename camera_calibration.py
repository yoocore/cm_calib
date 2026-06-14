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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple

import cv2
import numpy as np
from PIL import Image

from precheck_cli import run_precheck

from dde_health_check import (
    default_output_dir as _dde_default_output_dir,
    render_dde_execute_script,
    render_result_script,
    run_check_attempt,
)

try:
    import optuna
    _OPTUNA_AVAILABLE = True
except ImportError:
    _OPTUNA_AVAILABLE = False


_ANNOTATION_OCR_ENGINE = None


class _TeeStream:
    def __init__(self, primary: TextIO, secondary: TextIO):
        self._primary = primary
        self._secondary = secondary

    def write(self, data: str) -> int:
        written = self._primary.write(data)
        self._secondary.write(data)
        self.flush()
        return written

    def flush(self) -> None:
        self._primary.flush()
        try:
            self._secondary.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._primary.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return self._primary.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._primary, "encoding", "utf-8")

    def reconfigure(self, *args, **kwargs) -> None:
        if hasattr(self._primary, "reconfigure"):
            self._primary.reconfigure(*args, **kwargs)
        if hasattr(self._secondary, "reconfigure"):
            self._secondary.reconfigure(*args, **kwargs)


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


def _configure_live_log(cfg: dict, resume_from_result: bool) -> Path:
    output_dir = _resolve_config_output_dir(cfg)
    cfg["output_dir"] = str(output_dir)
    return _configure_live_log_for_output_dir(output_dir, resume_from_result)


def _unlink_if_exists(path: Path) -> None:
    for _ in range(3):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.05)


def _default_sim_output_root() -> Path:
    return Path("C:/CM_Projects/CMO141_Calibration/SimOutput")


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


def _resolve_config_output_dir(cfg: dict, config_path: Optional[Path] = None) -> Path:
    raw_output_dir = str(cfg.get("output_dir", "")).strip()
    if raw_output_dir:
        return Path(raw_output_dir)
    return _default_sim_output_root() / _default_output_name_from_config(config_path)


def _build_isolated_output_dir(prefix: str, camera_parent: Optional[str] = None) -> Path:
    """Build an isolated output directory under SimOutput.

    If `camera_parent` is provided, the returned path will be
    `SimOutput / camera_parent / {prefix}_{ts}` so that runs for the
    same camera are grouped under the same parent directory.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if camera_parent:
        return _default_sim_output_root() / camera_parent / f"{prefix}_{ts}"
    return _default_sim_output_root() / f"{prefix}_{ts}"


def _camera_name_from_config_path(config_path: Optional[Path]) -> str:
    return _default_output_name_from_config(config_path)


def _default_bootstrap_template_path() -> Path:
    calibration_dir = Path(__file__).resolve().parent
    preferred = calibration_dir / "configs" / "bootstrap.template.json"
    if preferred.exists():
        return preferred
    legacy_preferred = calibration_dir / "bootstrap.template.json"
    if legacy_preferred.exists():
        return legacy_preferred
    return calibration_dir / "config.bootstrap_template.json"


def _default_parameter_config() -> Dict[str, dict]:
    return {
        "pos_x": {
            "step": 0.002,
            "min_step": 0.001,
            "decimals": 4,
        },
        "pos_y": {
            "step": 0.001,
            "min_step": 0.001,
            "decimals": 4,
        },
        "pos_z": {
            "step": 0.002,
            "min_step": 0.001,
            "decimals": 4,
        },
        "yaw": {
            "step": 0.01,
            "min_step": 0.002,
            "decimals": 4,
        },
        "pitch": {
            "step": 0.02,
            "min_step": 0.002,
            "decimals": 4,
        },
        "roll": {
            "step": 0.01,
            "min_step": 0.002,
            "decimals": 4,
        },
        "lens_fov": {
            "step": 0.2,
            "min_step": 0.1,
            "decimals": 1,
        },
        "lens_scale": {
            "step": 0.005,
            "min_step": 0.005,
            "decimals": 3,
            "bounds_multiplier": 0,
        },
        "lens_offset_x": {
            "step": 0.01,
            "min_step": 0.01,
            "decimals": 2,
            "bounds_multiplier": 0,
        },
        "lens_offset_y": {
            "step": 0.01,
            "min_step": 0.01,
            "decimals": 2,
            "bounds_multiplier": 0,
        },
    }


def _default_parameter_order() -> List[str]:
    return [
        "pos_z",
        "pitch",
        "yaw",
        "pos_x",
        "roll",
        "pos_y",
        "lens_fov",
        "lens_scale",
        "lens_offset_x",
        "lens_offset_y",
    ]


def _default_bootstrap_config() -> dict:
    calibration_root = Path(__file__).resolve().parent
    repo_root = Path(__file__).resolve().parents[3]
    return {
        "settings_input_mode": "script_control",
        "script_control_script_path": str((calibration_root / "script_control_apply.tcl").resolve().as_posix()),
        "script_control_result_path": str((repo_root / "SimOutput" / "script_control_camera_apply_result.txt").resolve().as_posix()),
        "script_control_dde_service": "TclEval",
        "script_control_dde_topic": "CarMaker",
        "script_control_timeout_sec": 8.0,
        "script_control_settle_sec": 0.2,
        "template_feature_max_dim": 2048,
        "comparison_mode": "direct",
        "overlay_residual_threshold": 12,
        "overlay_residual_blur": 0,
        "keep_aspect_resize": True,
        "verify_all_coordinate_fields": False,
        "stop_after_first_accepted_direction": True,
        "progress_flush_every": 1,
        "settle_sec": 0.35,
        "target_score": 5.0,
        "acceptance_criteria": {
            "bottleneck_board_score_max_threshold": 4.0,
            "bottleneck_board_score_avg_threshold": 2.5,
        },
        "max_iters": 180,
        "min_improve": 5e-05,
        "step_decay": 0.7,
        "priority_board_acceptance": {
            "board_ids": [],
            "min_board_score_improvement": 0.75,
            "max_total_score_worsen": 1.0,
            "min_board_count": 1,
            "min_total_board_score_improvement": 0.0,
            "total_worsen_tradeoff_ratio": 0.0,
        },
        "joint_exploration": {
            "apply_to_all_params": True,
            "max_single_score_worsen": 2.5,
            "trial_multipliers": [1.0, 2.0, 4.0],
        },
        "strategy_adaptation": {
            "enabled": False,
            "reorder_params": True,
            "adjust_step_scale": True,
            "focus_on_joint_candidates": True,
            "bottleneck_board_awareness": True,
            "bottleneck_top_k": 2,
            "bottleneck_min_improvement": 0.1,
            "bottleneck_priority_boost": 1.25,
            "priority_decay": 0.82,
            "accepted_priority_boost": 2.5,
            "joint_candidate_priority_boost": 0.75,
            "rejected_priority_penalty": 0.15,
            "step_scale_up": 1.35,
            "step_scale_down": 0.85,
            "stagnation_patience": 2,
            "stagnation_step_scale_up": 1.2,
            "min_step_scale": 0.5,
            "max_step_scale": 3.0,
            "exploration_profiles": [
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
            ],
        },
        "optimization_order": _default_parameter_order(),
        "parameters": _default_parameter_config(),
    }


def _deep_merge_dict(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _resolved_bootstrap_config(template_cfg: dict) -> dict:
    cfg = _deep_merge_dict(_default_bootstrap_config(), template_cfg)
    return cfg


def _path_to_json_string(path: Path) -> str:
    return path.resolve().as_posix()


def _bootstrap_partial_template_dir(real_image_path: Path, camera_name: str) -> Path:
    return real_image_path.resolve().parent / "bootstrap_templates" / camera_name


def _is_custom_marker_board_type(board_type: str) -> bool:
    return str(board_type).strip().lower() in {"custom_groundmaker", "custom_maker"}


def _is_aruco_family_board_type(board_type: str) -> bool:
    return str(board_type).strip().lower() in {"aruco", "charuco"}


def _is_apriltag_board_type(board_type: str) -> bool:
    return str(board_type).strip().lower() == "apriltag"


def _preprocess_auto_template_match_image(
    gray_image: np.ndarray,
    binary_threshold: int,
) -> np.ndarray:
    if binary_threshold > 0:
        _, processed = cv2.threshold(
            gray_image,
            float(binary_threshold),
            255,
            cv2.THRESH_BINARY_INV,
        )
        return processed.astype(np.uint8)
    return gray_image.astype(np.uint8)


def _masked_secondary_response_max(
    response: np.ndarray,
    best_location: Tuple[int, int],
    template_shape: Tuple[int, int],
) -> float:
    if response.size <= 1:
        return float("-inf")
    mask = np.ones(response.shape, dtype=bool)
    best_x, best_y = best_location
    template_h, template_w = template_shape
    radius_x = max(1, template_w // 3)
    radius_y = max(1, template_h // 3)
    x0 = max(0, best_x - radius_x)
    y0 = max(0, best_y - radius_y)
    x1 = min(response.shape[1], best_x + radius_x + 1)
    y1 = min(response.shape[0], best_y + radius_y + 1)
    mask[y0:y1, x0:x1] = False
    if not mask.any():
        return float("-inf")
    return float(np.max(response[mask]))


def _select_auto_template_crop(
    roi_gray: np.ndarray,
    binary_threshold: int,
) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    roi_h, roi_w = roi_gray.shape[:2]
    if roi_h <= 0 or roi_w <= 0:
        raise ValueError("ROI image for auto template crop is empty")
    if roi_h < 24 or roi_w < 24:
        return roi_gray.copy(), (0, 0, roi_w, roi_h)

    processed = _preprocess_auto_template_match_image(roi_gray, binary_threshold)
    candidate_sizes: List[Tuple[int, int]] = []
    for width_ratio, height_ratio in (
        (0.18, 0.18),
        (0.22, 0.22),
        (0.28, 0.22),
        (0.22, 0.28),
        (0.32, 0.24),
        (0.24, 0.32),
    ):
        crop_w = max(18, min(roi_w - 2, int(round(roi_w * width_ratio))))
        crop_h = max(18, min(roi_h - 2, int(round(roi_h * height_ratio))))
        if crop_w >= roi_w or crop_h >= roi_h:
            continue
        size = (crop_w, crop_h)
        if size not in candidate_sizes:
            candidate_sizes.append(size)

    best_score = float("-inf")
    best_bbox: Optional[Tuple[int, int, int, int]] = None
    for crop_w, crop_h in candidate_sizes:
        step_x = max(1, (roi_w - crop_w) // 4)
        step_y = max(1, (roi_h - crop_h) // 4)
        x_positions = sorted({0, max(0, roi_w - crop_w), *range(0, max(1, roi_w - crop_w + 1), step_x)})
        y_positions = sorted({0, max(0, roi_h - crop_h), *range(0, max(1, roi_h - crop_h + 1), step_y)})
        for x in x_positions:
            for y in y_positions:
                patch_gray = roi_gray[y : y + crop_h, x : x + crop_w]
                patch_processed = processed[y : y + crop_h, x : x + crop_w]
                if patch_gray.size == 0 or patch_processed.size == 0:
                    continue

                lap_var = float(cv2.Laplacian(patch_gray, cv2.CV_32F).var())
                if lap_var < 5.0:
                    continue

                response = cv2.matchTemplate(processed, patch_processed, cv2.TM_CCOEFF_NORMED)
                _, best_value, _, best_location = cv2.minMaxLoc(response)
                second_best = _masked_secondary_response_max(
                    response,
                    best_location,
                    (crop_h, crop_w),
                )
                uniqueness = float(best_value) - second_best if math.isfinite(second_best) else float(best_value)
                center_bias = 1.0 - (
                    abs((x + crop_w * 0.5) - roi_w * 0.5) / max(roi_w, 1)
                    + abs((y + crop_h * 0.5) - roi_h * 0.5) / max(roi_h, 1)
                )
                size_penalty = 0.35 * ((crop_w / roi_w) + (crop_h / roi_h))
                score = uniqueness * 4.0 + min(lap_var / 180.0, 3.0) + center_bias - size_penalty
                if score > best_score:
                    best_score = score
                    best_bbox = (x, y, crop_w, crop_h)

    if best_bbox is None:
        fallback_w = max(18, min(roi_w, int(round(roi_w * 0.3))))
        fallback_h = max(18, min(roi_h, int(round(roi_h * 0.3))))
        x = max(0, (roi_w - fallback_w) // 2)
        y = max(0, (roi_h - fallback_h) // 2)
        best_bbox = (x, y, fallback_w, fallback_h)

    x, y, crop_w, crop_h = best_bbox
    return roi_gray[y : y + crop_h, x : x + crop_w].copy(), best_bbox


def _materialize_auto_template_image(
    real_gray: np.ndarray,
    roi: Tuple[int, int, int, int],
    binary_threshold: int,
    template_path: Path,
    manual_crop: Optional[Tuple[int, int, int, int]] = None,
) -> Tuple[Path, Tuple[int, int, int, int]]:
    x, y, width, height = [int(value) for value in roi]
    roi_gray = real_gray[y : y + height, x : x + width]
    if roi_gray.size == 0:
        raise RuntimeError(f"Cannot auto-generate template from empty ROI: {list(roi)}")
    if manual_crop is not None:
        crop_x, crop_y, crop_w, crop_h = [int(value) for value in manual_crop]
        crop_x0 = max(0, crop_x)
        crop_y0 = max(0, crop_y)
        crop_x1 = min(roi_gray.shape[1], crop_x + crop_w)
        crop_y1 = min(roi_gray.shape[0], crop_y + crop_h)
        if crop_x0 >= crop_x1 or crop_y0 >= crop_y1:
            raise RuntimeError(f"Manual template_source_crop is outside ROI bounds: {list(manual_crop)}")
        template_crop = (crop_x0, crop_y0, crop_x1 - crop_x0, crop_y1 - crop_y0)
        template_image = roi_gray[crop_y0:crop_y1, crop_x0:crop_x1].copy()
    else:
        template_image, template_crop = _select_auto_template_crop(roi_gray, binary_threshold)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(template_path), template_image):
        raise RuntimeError(f"Failed to write auto template image: {template_path}")
    return template_path, template_crop


def _derive_camera_name_from_image_path(image_path: Path) -> str:
    stem = image_path.stem
    stem = re.sub(r"_origin$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^\d+_", "", stem)
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_")
    if not candidate:
        raise ValueError(f"Cannot derive camera name from image path: {image_path}")
    return candidate.lower()


def _board_prototype_family(board_id: str) -> Optional[str]:
    normalized = str(board_id).strip().upper().replace("-", "_")
    compact = re.sub(r"[^A-Za-z0-9]+", "", str(board_id).strip()).upper()
    generic_match = re.fullmatch(r"([A-Z]+)(\d+)", compact)
    if generic_match:
        return generic_match.group(1)
    if re.fullmatch(r"B\d+", normalized):
        return "B"
    if re.fullmatch(r"S\d+", normalized):
        return "S"
    if normalized in {"G1_L", "G1_LEFT", "G1LEFT", "G1_LEFT_MARK", "G1LEFTMARK"}:
        return "G1_LEFT"
    if normalized in {"G1_C", "G1_CENTER", "G1CENTRE", "G1_CENTER_CIRCLE", "G1CENTERCIRCLE"}:
        return "G1_CENTER"
    if normalized in {"G1_R", "G1_RIGHT", "G1RIGHT", "G1_RIGHT_MARK", "G1RIGHTMARK"}:
        return "G1_RIGHT"
    if normalized.startswith("G1") and "LEFT" in normalized:
        return "G1_LEFT"
    if normalized.startswith("G1") and ("CENTER" in normalized or "CENTRE" in normalized or normalized.endswith("_C")):
        return "G1_CENTER"
    if normalized.startswith("G1") and "RIGHT" in normalized:
        return "G1_RIGHT"
    return None


def _get_annotation_ocr_engine():
    global _ANNOTATION_OCR_ENGINE

    if _ANNOTATION_OCR_ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:
            raise RuntimeError(
                "Annotation bootstrap requires the rapidocr-onnxruntime package. "
                "Install it in the CameraCalibration Python environment before using "
                "--bootstrap-config-from-annotation."
            ) from exc
        _ANNOTATION_OCR_ENGINE = RapidOCR()
    return _ANNOTATION_OCR_ENGINE


def _normalize_annotation_board_id(text: str, detected_group: str) -> Optional[str]:
    compact = re.sub(r"[^A-Za-z0-9]+", "", str(text).strip()).upper()
    if not compact:
        return None

    if detected_group == "B":
        candidate = compact.replace("I", "1").replace("L", "1")
        if re.fullmatch(r"B\d+", candidate):
            return f"B{int(candidate[1:])}"
        return None

    if detected_group == "S":
        candidate = compact.replace("I", "1").replace("L", "1")
        if re.fullmatch(r"S\d+", candidate):
            return f"S{int(candidate[1:])}"
        if re.fullmatch(r"[35]\d+", candidate):
            return f"S{int(candidate[1:])}"
        return None

    if detected_group == "G1":
        candidate = compact.replace("GI", "G1")
        if re.fullmatch(r"G1(L|LEFT|LEFTMARK)", candidate):
            return "G1_left"
        if re.fullmatch(r"G1(C|CENTER|CENTRE|CENTERCIRCLE)", candidate):
            return "G1_center"
        if re.fullmatch(r"G1(R|RIGHT|RIGHTMARK)", candidate):
            return "G1_right"
        return None

    generic_group = re.sub(r"[^A-Za-z0-9]+", "", str(detected_group).strip()).upper()
    if generic_group and generic_group not in {"B", "S", "G1"}:
        candidate = compact
        if candidate.startswith(generic_group):
            suffix = candidate[len(generic_group) :]
            suffix = suffix.replace("I", "1").replace("L", "1")
            if suffix.isdigit():
                return f"{generic_group}{int(suffix)}"

    return None


def _run_annotation_ocr(
    image: np.ndarray,
    source_name: str,
    origin: Tuple[int, int] = (0, 0),
    scale: float = 1.0,
) -> List[dict]:
    ocr_engine = _get_annotation_ocr_engine()
    result, _ = ocr_engine(image)
    detections: List[dict] = []
    for item in result or []:
        if len(item) < 3:
            continue
        box, text, score = item
        confidence = float(score)
        if confidence < 0.45:
            continue

        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        min_x = origin[0] + min(xs) / max(scale, 1e-6)
        min_y = origin[1] + min(ys) / max(scale, 1e-6)
        max_x = origin[0] + max(xs) / max(scale, 1e-6)
        max_y = origin[1] + max(ys) / max(scale, 1e-6)
        detections.append(
            {
                "text": str(text),
                "score": confidence,
                "bbox": (min_x, min_y, max_x - min_x, max_y - min_y),
                "source": source_name,
            }
        )
    return detections


def _rect_gap_distance(
    rect_a: Tuple[float, float, float, float],
    rect_b: Tuple[float, float, float, float],
) -> float:
    ax1, ay1, aw, ah = rect_a
    bx1, by1, bw, bh = rect_b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    gap_x = max(ax1 - bx2, bx1 - ax2, 0.0)
    gap_y = max(ay1 - by2, by1 - ay2, 0.0)
    if gap_x <= 0.0 and gap_y <= 0.0:
        return 0.0
    return math.hypot(gap_x, gap_y)


def _assign_annotation_board_ids(
    grouped: Dict[str, List[Tuple[int, int, int, int]]],
    detections: List[dict],
    group_candidates: Dict[str, List[str]],
) -> Dict[Tuple[int, int, int, int], str]:
    assignments: Dict[Tuple[int, int, int, int], str] = {}
    for detected_group, rects in grouped.items():
        candidate_groups = group_candidates.get(detected_group, [detected_group])
        candidate_rows: List[Tuple[float, Tuple[int, int, int, int], str]] = []
        for detection in detections:
            board_ids = [
                board_id
                for board_id in (
                    _normalize_annotation_board_id(detection.get("text", ""), candidate_group)
                    for candidate_group in candidate_groups
                )
                if board_id
            ]
            if not board_ids:
                continue

            label_bbox = detection["bbox"]
            label_center_x = float(label_bbox[0]) + float(label_bbox[2]) / 2.0
            label_center_y = float(label_bbox[1]) + float(label_bbox[3]) / 2.0
            for rect in rects:
                max_gap = max(140.0, 0.75 * float(max(rect[2], rect[3])))
                if detected_group == "G1":
                    max_gap = max(max_gap, 220.0)
                gap = _rect_gap_distance(
                    (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])),
                    label_bbox,
                )
                if gap > max_gap:
                    continue

                rect_area = float(rect[2] * rect[3])
                label_inside_rect = (
                    float(rect[0]) <= label_center_x <= float(rect[0] + rect[2])
                    and float(rect[1]) <= label_center_y <= float(rect[1] + rect[3])
                )
                source_bonus = 6.0 if str(detection.get("source", "")).startswith("local") else 0.0
                containment_bonus = 30.0 if label_inside_rect else 0.0
                area_penalty = rect_area / 50000.0 if label_inside_rect else 0.0
                ranking = (
                    float(detection["score"]) * 1000.0
                    - gap * 3.0
                    + source_bonus
                    + containment_bonus
                    - area_penalty
                )
                for board_id in board_ids:
                    candidate_rows.append((ranking, rect, board_id))

        candidate_rows.sort(key=lambda item: item[0], reverse=True)
        used_ids: set = set()
        used_rects: set = set()
        for _, rect, board_id in candidate_rows:
            if rect in used_rects or board_id in used_ids:
                continue
            assignments[rect] = board_id
            used_rects.add(rect)
            used_ids.add(board_id)

    return assignments


def _extract_annotation_board_ids(
    annotated_image_path: Path,
    grouped: Dict[str, List[Tuple[int, int, int, int]]],
    candidate_group_keys: Optional[List[str]] = None,
) -> Dict[Tuple[int, int, int, int], str]:
    annotated = cv2.imread(str(annotated_image_path))
    if annotated is None:
        raise FileNotFoundError(f"Failed to read annotated image: {annotated_image_path}")

    group_candidates = {
        detected_group: list(candidate_group_keys or [])
        if detected_group == "__ANNOTATION__"
        else [detected_group]
        for detected_group in grouped.keys()
    }

    detections: List[dict] = []
    detections.extend(_run_annotation_ocr(annotated, source_name="global_color"))

    gray = cv2.cvtColor(annotated, cv2.COLOR_BGR2GRAY)
    gray_up2 = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    detections.extend(
        _run_annotation_ocr(
            cv2.cvtColor(gray_up2, cv2.COLOR_GRAY2BGR),
            source_name="global_gray_up2",
            scale=2.0,
        )
    )

    assignments = _assign_annotation_board_ids(grouped, detections, group_candidates)
    unresolved = [
        (detected_group, rect)
        for detected_group, rects in grouped.items()
        for rect in rects
        if rect not in assignments
    ]

    for detected_group, rect in unresolved:
        x, y, width, height = rect
        focused_crop = annotated[y : y + height, x : x + width]
        if focused_crop.size != 0:
            detections.extend(
                _run_annotation_ocr(
                    focused_crop,
                    source_name=f"focused_{detected_group}_color",
                    origin=(x, y),
                )
            )

            focused_gray = cv2.cvtColor(focused_crop, cv2.COLOR_BGR2GRAY)
            focused_gray_up4 = cv2.resize(
                focused_gray,
                None,
                fx=4.0,
                fy=4.0,
                interpolation=cv2.INTER_CUBIC,
            )
            detections.extend(
                _run_annotation_ocr(
                    cv2.cvtColor(focused_gray_up4, cv2.COLOR_GRAY2BGR),
                    source_name=f"focused_{detected_group}_gray_up4",
                    origin=(x, y),
                    scale=4.0,
                )
            )

        pad_x = max(120, int(round(width * 0.9)))
        pad_y = max(120, int(round(height * 0.9)))
        x0 = max(0, x - pad_x)
        y0 = max(0, y - pad_y)
        x1 = min(annotated.shape[1], x + width + pad_x)
        y1 = min(annotated.shape[0], y + height + pad_y)
        crop = annotated[y0:y1, x0:x1]
        if crop.size == 0:
            continue

        detections.extend(
            _run_annotation_ocr(
                crop,
                source_name=f"local_{detected_group}_color",
                origin=(x0, y0),
            )
        )

        crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        crop_gray_up4 = cv2.resize(crop_gray, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)
        detections.extend(
            _run_annotation_ocr(
                cv2.cvtColor(crop_gray_up4, cv2.COLOR_GRAY2BGR),
                source_name=f"local_{detected_group}_gray_up4",
                origin=(x0, y0),
                scale=4.0,
            )
        )

    assignments = _assign_annotation_board_ids(grouped, detections, group_candidates)
    unresolved = [
        (detected_group, rect)
        for detected_group, rects in grouped.items()
        for rect in rects
        if rect not in assignments
    ]
    if unresolved:
        unresolved_text = ", ".join(
            f"{detected_group}:{list(rect)}" for detected_group, rect in unresolved
        )
        raise RuntimeError(
            "Failed to read board IDs from the annotated image for: "
            f"{unresolved_text}. Please check that each board label is visible in the annotation."
        )
    return assignments


def _extract_annotation_rectangles(annotated_image_path: Path) -> List[Tuple[int, int, int, int]]:
    annotated = cv2.imread(str(annotated_image_path))
    if annotated is None:
        raise FileNotFoundError(f"Failed to read annotated image: {annotated_image_path}")

    red_mask = (
        (annotated[:, :, 2] > 180)
        & (annotated[:, :, 2] > annotated[:, :, 1] + 60)
        & (annotated[:, :, 2] > annotated[:, :, 0] + 60)
    ).astype(np.uint8) * 255
    image_height, image_width = red_mask.shape[:2]
    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    rectangles: List[Tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width < 25 or height < 25:
            continue

        area = float(cv2.contourArea(contour))
        fill_ratio = area / max(float(width * height), 1.0)
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.03 * perimeter, True)
        if len(approx) < 3 or len(approx) > 8:
            continue

        box_mask = red_mask[y : y + height, x : x + width]
        border = max(1, min(width, height) // 20)
        edge_ratios = [
            float(box_mask[:border, :].mean()) / 255.0,
            float(box_mask[-border:, :].mean()) / 255.0,
            float(box_mask[:, :border].mean()) / 255.0,
            float(box_mask[:, -border:].mean()) / 255.0,
        ]
        strong_edge_count = sum(value >= 0.20 for value in edge_ratios)
        moderate_edge_count = sum(value >= 0.08 for value in edge_ratios)
        max_edge_ratio = max(edge_ratios) if edge_ratios else 0.0
        touches_border = (
            x <= 1 or y <= 1 or x + width >= image_width - 1 or y + height >= image_height - 1
        )
        border_partial_box = touches_border and fill_ratio >= 0.03 and strong_edge_count >= 3
        large_outline_box = (
            max(width, height) >= 300
            and fill_ratio >= 0.03
            and moderate_edge_count >= 3
            and max_edge_ratio >= 0.18
        )
        if fill_ratio < 0.85 and not (border_partial_box or large_outline_box):
            continue

        rectangles.append((x, y, width, height))

    rectangles.sort(key=lambda item: (item[0], item[1], item[2] * item[3]), reverse=False)
    deduped: List[Tuple[int, int, int, int]] = []
    for candidate in rectangles:
        x1, y1, w1, h1 = candidate
        keep = True
        for existing in deduped:
            x2, y2, w2, h2 = existing
            inter_left = max(x1, x2)
            inter_top = max(y1, y2)
            inter_right = min(x1 + w1, x2 + w2)
            inter_bottom = min(y1 + h1, y2 + h2)
            if inter_right <= inter_left or inter_bottom <= inter_top:
                continue
            inter_area = float((inter_right - inter_left) * (inter_bottom - inter_top))
            union_area = float(w1 * h1 + w2 * h2) - inter_area
            if union_area > 0.0 and inter_area / union_area >= 0.90:
                keep = False
                break
        if keep:
            deduped.append(candidate)

    if not deduped:
        raise RuntimeError(
            f"No annotation rectangles were detected in {annotated_image_path}. "
            "Expected red rectangular board annotations."
        )
    return deduped


def _cluster_1d(values: np.ndarray, cluster_count: int) -> np.ndarray:
    if values.size == 0:
        return np.empty((0,), dtype=np.int32)
    effective_clusters = max(1, min(int(cluster_count), int(values.size)))
    centers = np.linspace(float(values.min()), float(values.max()), effective_clusters)
    labels = np.zeros(values.shape[0], dtype=np.int32)
    for _ in range(32):
        distances = np.abs(values[:, None] - centers[None, :])
        new_labels = np.argmin(distances, axis=1)
        new_centers = centers.copy()
        for index in range(effective_clusters):
            assigned = values[new_labels == index]
            if assigned.size > 0:
                new_centers[index] = float(assigned.mean())
        if np.array_equal(new_labels, labels) and np.allclose(new_centers, centers):
            break
        labels = new_labels
        centers = new_centers
    return labels


def _group_annotation_rectangles(
    rectangles: List[Tuple[int, int, int, int]],
) -> Dict[str, List[Tuple[int, int, int, int]]]:
    grouped: Dict[str, List[Tuple[int, int, int, int]]] = {"__ANNOTATION__": []}
    if not rectangles:
        return grouped

    grouped["__ANNOTATION__"] = sorted(
        rectangles,
        key=lambda rect: (rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0),
    )
    return grouped


def _load_bootstrap_template_specs(template_cfg: dict) -> Dict[str, List[dict]]:
    grouped_specs: Dict[str, List[dict]] = {}
    detected_group_aliases = {
        "B": ("B", ""),
        "S": ("S", ""),
        "G1": ("G1", ""),
        "G1_LEFT": ("G1", "G1_left"),
        "G1_CENTER": ("G1", "G1_center"),
        "G1_RIGHT": ("G1", "G1_right"),
    }
    raw_templates = template_cfg.get("bootstrap_templates")
    if isinstance(raw_templates, list) and raw_templates:
        for item in raw_templates:
            if not isinstance(item, dict):
                raise ValueError("bootstrap_templates entries must be objects")

            raw_detected_group = str(item.get("detected_group", "")).strip().upper()
            detected_group_info = detected_group_aliases.get(raw_detected_group)
            if detected_group_info is None:
                if re.fullmatch(r"[A-Z]+", raw_detected_group) and raw_detected_group not in {"B", "S"}:
                    detected_group_info = (raw_detected_group, "")
                else:
                    raise ValueError(
                        f"Unsupported bootstrap detected_group: {raw_detected_group!r}"
                    )
            detected_group, detected_group_board_id = detected_group_info
            grouped_specs.setdefault(detected_group, [])

            board_cfg = item.get("board")
            if not isinstance(board_cfg, dict) or not board_cfg:
                raise ValueError(
                    f"bootstrap_templates entry for {raw_detected_group} must provide board"
                )

            grouped_specs[detected_group].append(
                {
                    "detected_group": raw_detected_group,
                    "generated_id_prefix": str(item.get("generated_id_prefix", "")).strip(),
                    "generated_board_id": str(item.get("generated_board_id", "")).strip(),
                    "sort_rank": int(item.get("sort_rank", len(grouped_specs[detected_group]))),
                    "board": copy.deepcopy(board_cfg),
                }
            )

            latest = grouped_specs[detected_group][-1]
            board_type = str(board_cfg.get("board_type", "")).strip().lower()
            if board_type == "checkerboard" and not latest["generated_id_prefix"]:
                latest["generated_id_prefix"] = detected_group
            if detected_group not in {"G1"} and board_type != "checkerboard" and not latest["generated_id_prefix"]:
                latest["generated_id_prefix"] = detected_group
            if detected_group == "G1" and not latest["generated_board_id"]:
                latest["generated_board_id"] = detected_group_board_id
            if detected_group == "G1" and not latest["generated_board_id"]:
                latest["generated_board_id"] = str(board_cfg.get("board_id", "")).strip()
            if detected_group == "G1" and not latest["generated_board_id"]:
                raise ValueError(
                    "G1 bootstrap template entries must provide a family via detected_group (G1_LEFT/G1_CENTER/G1_RIGHT) or generated_board_id"
                )

        if not grouped_specs:
            raise ValueError("bootstrap_templates must define at least one group")
        return grouped_specs

    for board_cfg in template_cfg.get("boards", []):
        family = _board_prototype_family(str(board_cfg.get("board_id", "")))
        board_type = str(board_cfg.get("board_type", "")).strip().lower()
        if family == "B" and not grouped_specs.get("B"):
            grouped_specs["B"] = [
                {
                    "detected_group": "B",
                    "generated_id_prefix": "B",
                    "generated_board_id": "",
                    "sort_rank": 0,
                    "board": copy.deepcopy(board_cfg),
                }
            ]
        elif family == "S" and not grouped_specs.get("S"):
            grouped_specs["S"] = [
                {
                    "detected_group": "S",
                    "generated_id_prefix": "S",
                    "generated_board_id": "",
                    "sort_rank": 0,
                    "board": copy.deepcopy(board_cfg),
                }
            ]
        elif family in {"G1_LEFT", "G1_CENTER", "G1_RIGHT"}:
            generated_board_id = str(board_cfg.get("board_id", "")).strip()
            sort_rank = {"G1_LEFT": 0, "G1_CENTER": 1, "G1_RIGHT": 2}[family]
            exists = any(
                spec["generated_board_id"] == generated_board_id
                for spec in grouped_specs.setdefault("G1", [])
            )
            if not exists:
                grouped_specs["G1"].append(
                    {
                        "detected_group": "G1",
                        "generated_id_prefix": "",
                        "generated_board_id": generated_board_id,
                        "sort_rank": sort_rank,
                        "board": copy.deepcopy(board_cfg),
                    }
                )
        elif family and board_type == "checkerboard" and family not in {"B", "S"} and family not in grouped_specs:
            grouped_specs[family] = [
                {
                    "detected_group": family,
                    "generated_id_prefix": family,
                    "generated_board_id": "",
                    "sort_rank": 0,
                    "board": copy.deepcopy(board_cfg),
                }
            ]
        elif family and family not in grouped_specs:
            grouped_specs[family] = [
                {
                    "detected_group": family,
                    "generated_id_prefix": family,
                    "generated_board_id": "",
                    "sort_rank": 0,
                    "board": copy.deepcopy(board_cfg),
                }
            ]

    if not grouped_specs:
        raise RuntimeError("Template config is missing bootstrap prototypes")
    return grouped_specs


def _build_boards_from_annotation_rectangles(
    template_cfg: dict,
    rectangles: List[Tuple[int, int, int, int]],
    annotated_image_path: Path,
) -> List[dict]:
    bootstrap_specs = _load_bootstrap_template_specs(template_cfg)
    candidate_group_keys = list(bootstrap_specs.keys())
    if not candidate_group_keys:
        raise RuntimeError("Bootstrap template currently requires at least one detected_group family")

    grouped = _group_annotation_rectangles(rectangles)
    recognized_board_ids = _extract_annotation_board_ids(
        annotated_image_path,
        grouped,
        candidate_group_keys=candidate_group_keys,
    )

    def _sort_rectangles_by_column_then_row(
        items: List[Tuple[int, int, int, int]]
    ) -> List[Tuple[int, int, int, int]]:
        if not items:
            return []
        avg_width = float(np.mean([rect[2] for rect in items]))
        column_threshold = max(24.0, avg_width * 0.35)
        sorted_by_x = sorted(items, key=lambda rect: rect[0] + rect[2] / 2.0)
        columns: List[List[Tuple[int, int, int, int]]] = []
        column_centers: List[float] = []
        for rect in sorted_by_x:
            center_x = rect[0] + rect[2] / 2.0
            if not columns or abs(center_x - column_centers[-1]) > column_threshold:
                columns.append([rect])
                column_centers.append(center_x)
                continue
            columns[-1].append(rect)
            column_centers[-1] = float(
                np.mean([item[0] + item[2] / 2.0 for item in columns[-1]])
            )

        ordered: List[Tuple[int, int, int, int]] = []
        for column in columns:
            ordered.extend(sorted(column, key=lambda rect: rect[1] + rect[3] / 2.0))
        return ordered

    generated_boards: List[dict] = []

    family_specs: Dict[str, dict] = {}
    family_order: Dict[str, Tuple[int, int]] = {}
    for group_index, (group, specs) in enumerate(bootstrap_specs.items()):
        if not specs:
            continue
        board_type = str(specs[0]["board"].get("board_type", "")).strip().lower()
        if board_type == "checkerboard":
            if len(specs) != 1 or not specs[0]["generated_id_prefix"]:
                raise RuntimeError(
                    f"Bootstrap template must define exactly one {group} entry with generated_id_prefix"
                )
            family_specs[group] = specs[0]
            family_order[group] = (group_index, 0)
            continue

        if group == "G1":
            for spec in specs:
                family = _board_prototype_family(str(spec.get("generated_board_id", "")))
                if family is None:
                    raise RuntimeError("G1 bootstrap template entries must define a recognizable board.board_id")
                if family in family_specs:
                    raise RuntimeError(f"Duplicate bootstrap template family detected: {family}")
                family_specs[family] = spec
                family_order[family] = (group_index, int(spec.get("sort_rank", 0)))
            continue

        if len(specs) != 1 or not specs[0].get("generated_id_prefix"):
            raise RuntimeError(
                f"Bootstrap template must define exactly one {group} entry with generated_id_prefix"
            )
        family_specs[group] = specs[0]
        family_order[group] = (group_index, 0)

    all_rects = _sort_rectangles_by_column_then_row(grouped["__ANNOTATION__"])

    for rect in all_rects:
        recognized_board_id = recognized_board_ids[rect]
        family = _board_prototype_family(recognized_board_id)
        if family not in family_specs:
            raise RuntimeError(
                f"No bootstrap template prototype matches recognized board ID {recognized_board_id!r}"
            )
        spec = family_specs[family]

        board_cfg = copy.deepcopy(spec["board"])
        board_cfg["board_id"] = recognized_board_id
        board_cfg["roi"] = [int(value) for value in rect]
        if str(board_cfg.get("board_type", "")).strip().lower() == "custom_maker":
            board_cfg["template_source_roi"] = [int(value) for value in rect]
            board_cfg["template_source_crop"] = [0, 0, int(rect[2]), int(rect[3])]
        generated_boards.append(board_cfg)

    def _board_sort_key(board_cfg: dict) -> Tuple[int, int, int]:
        board_id = str(board_cfg.get("board_id", ""))
        family = _board_prototype_family(board_id) or ""
        if family in family_order:
            group_rank, family_rank = family_order[family]
            return (group_rank, family_rank, int(re.sub(r"\D", "", board_id) or 0))
        generic_match = re.fullmatch(r"([A-Za-z]+)(\d+)", board_id)
        if generic_match:
            return (len(family_order), 0, int(generic_match.group(2)))
        groundmaker_rank = {"G1_left": 0, "G1_center": 1, "G1_right": 2}
        return (len(family_order), groundmaker_rank.get(board_id, 99), 0)

    return sorted(generated_boards, key=_board_sort_key)


def _auto_upgrade_partial_checkerboards(
    cfg: dict,
    config_path: Path,
    real_image_path: Path,
    camera_name: str,
) -> List[str]:
    calibrator = CameraCalibrator(copy.deepcopy(cfg), config_path=config_path)
    board_cfg_by_id = {
        str(board_cfg.get("board_id", "")).strip(): board_cfg
        for board_cfg in cfg.get("boards", [])
        if isinstance(board_cfg, dict)
    }
    template_dir = _bootstrap_partial_template_dir(real_image_path, camera_name)
    template_dir.mkdir(parents=True, exist_ok=True)

    upgraded_board_ids: List[str] = []
    for board in calibrator.boards:
        if board.board_type != "checkerboard" or board.roi is None:
            continue

        primary = calibrator._detect_checkerboard(calibrator.real_img, board)
        if calibrator._is_visible(primary, board.min_detected_points):
            continue

        board_cfg = board_cfg_by_id.get(board.board_id)
        if board_cfg is None:
            continue

        template_name = re.sub(r"[^A-Za-z0-9_]+", "_", board.board_id).strip("_") or "checkerboard"
        template_path = template_dir / f"{template_name.lower()}_partial.png"
        _materialize_auto_template_image(
            calibrator.real_img,
            board.roi,
            int(board_cfg.get("template_binary_threshold", 150)),
            template_path,
        )

        previous_values = {
            "template_image": board_cfg.get("template_image"),
            "template_match_threshold": board_cfg.get("template_match_threshold"),
            "template_binary_threshold": board_cfg.get("template_binary_threshold"),
            "min_detected_points": board_cfg.get("min_detected_points"),
        }
        board_cfg["template_image"] = _path_to_json_string(template_path)
        board_cfg["custom_detector"] = "template_match"
        board_cfg["template_match_threshold"] = float(board_cfg.get("template_match_threshold", 0.45))
        board_cfg["template_binary_threshold"] = int(board_cfg.get("template_binary_threshold", 150))
        current_min_detected_points = board_cfg.get("min_detected_points")
        if current_min_detected_points is None:
            board_cfg["min_detected_points"] = 9
        else:
            board_cfg["min_detected_points"] = min(int(current_min_detected_points), 9)

        trial_calibrator = CameraCalibrator(copy.deepcopy(cfg), config_path=config_path)
        trial_board = next((item for item in trial_calibrator.boards if item.board_id == board.board_id), None)
        if trial_board is None:
            continue
        upgraded_detection = trial_calibrator._detect_board(trial_calibrator.real_img, trial_board)
        if trial_calibrator._is_visible(upgraded_detection, trial_board.min_detected_points):
            upgraded_board_ids.append(board.board_id)
            continue

        for key, value in previous_values.items():
            if value is None:
                board_cfg.pop(key, None)
            else:
                board_cfg[key] = value
        _unlink_if_exists(template_path)

    return upgraded_board_ids


def _sync_materialized_board_fields_from_calibrator(cfg: dict, calibrator: "CameraCalibrator") -> None:
    board_cfg_by_id = {
        str(board_cfg.get("board_id", "")).strip(): board_cfg
        for board_cfg in cfg.get("boards", [])
        if isinstance(board_cfg, dict)
    }
    for board in calibrator.boards:
        board_cfg = board_cfg_by_id.get(board.board_id)
        if board_cfg is None:
            continue
        if board.template_image:
            board_cfg["template_image"] = str(board.template_image)
        if board.template_source_roi is not None:
            board_cfg["template_source_roi"] = [int(value) for value in board.template_source_roi]
        if board.template_source_crop is not None:
            board_cfg["template_source_crop"] = [int(value) for value in board.template_source_crop]


def bootstrap_config_from_annotation(
    template_config_path: Path,
    real_image_path: Path,
    annotated_image_path: Path,
    output_path: Optional[Path] = None,
    preview_path: Optional[Path] = None,
    camera_name: Optional[str] = None,
) -> Tuple[Path, Path, List[dict]]:
    with open(template_config_path, "r", encoding="utf-8-sig") as f:
        template_cfg = json.load(f)

    cfg = _resolved_bootstrap_config(template_cfg)

    resolved_real_image = real_image_path.resolve()
    resolved_annotated_image = annotated_image_path.resolve()
    resolved_camera_name = camera_name or _derive_camera_name_from_image_path(resolved_real_image)
    output_file = output_path or template_config_path.with_name(f"camera.{resolved_camera_name}.json")
    preview_file = preview_path or (_default_sim_output_root() / resolved_camera_name / "annotation_bootstrap_preview.png")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    preview_file.parent.mkdir(parents=True, exist_ok=True)

    rectangles = _extract_annotation_rectangles(resolved_annotated_image)
    generated_boards = _build_boards_from_annotation_rectangles(
        cfg,
        rectangles,
        resolved_annotated_image,
    )

    cfg["real_image"] = _path_to_json_string(resolved_real_image)
    cfg.pop("output_dir", None)
    cfg.pop("bootstrap_templates", None)
    cfg["boards"] = generated_boards
    auto_upgraded_checkerboards = _auto_upgrade_partial_checkerboards(
        cfg,
        output_file,
        resolved_real_image,
        resolved_camera_name,
    )
    priority_accept_cfg = cfg.get("priority_board_acceptance")
    if isinstance(priority_accept_cfg, dict) and not priority_accept_cfg.get("board_ids"):
        priority_accept_cfg["board_ids"] = [
            str(board.get("board_id", ""))
            for board in generated_boards
            if _is_custom_marker_board_type(str(board.get("board_type", "")))
        ]
    bootstrap_calibrator = CameraCalibrator(cfg, config_path=output_file)
    _sync_materialized_board_fields_from_calibrator(cfg, bootstrap_calibrator)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

    preview_image = cv2.imread(str(resolved_real_image))
    if preview_image is None:
        raise FileNotFoundError(f"Failed to read real image: {resolved_real_image}")
    palette = {
        "B": (70, 80, 230),
        "S": (60, 170, 90),
        "G1": (220, 110, 60),
    }
    for board_cfg in generated_boards:
        x, y, width, height = [int(value) for value in board_cfg["roi"]]
        family = _board_prototype_family(str(board_cfg.get("board_id", ""))) or "B"
        family_prefix = "G1" if family.startswith("G1") else family
        color = palette.get(family_prefix, (200, 200, 70))
        cv2.rectangle(preview_image, (x, y), (x + width, y + height), color, 3)
        cv2.putText(
            preview_image,
            str(board_cfg["board_id"]),
            (x, max(24, y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
            cv2.LINE_AA,
        )

    cv2.imwrite(str(preview_file), preview_image)
    print(f"Bootstrapped config: {output_file}")
    print(f"Bootstrap preview image: {preview_file}")
    print(f"Detected annotation rectangles: {len(rectangles)}")
    if auto_upgraded_checkerboards:
        print(
            "Bootstrap auto-upgraded partial checkerboards: "
            + ", ".join(auto_upgraded_checkerboards)
        )
    for board_cfg in generated_boards:
        print(f"{board_cfg['board_id']}: roi={board_cfg['roi']}")
    return output_file, preview_file, generated_boards


def _camera_history_summary_path(camera_name: str) -> Path:
    return _default_sim_output_root() / _canonical_camera_group_name(camera_name) / "camera_summary.json"


def _camera_history_summary_compact_path(camera_name: str) -> Path:
    return _default_sim_output_root() / _canonical_camera_group_name(camera_name) / "camera_summary_compact.json"


def _iter_camera_history_dirs(camera_name: str) -> List[Path]:
    root = _default_sim_output_root()
    camera_root = root / _canonical_camera_group_name(camera_name)
    if not camera_root.exists() or not camera_root.is_dir():
        return []
    return [camera_root]


def _canonical_camera_group_name(name: str) -> str:
    raw_name = str(name).strip()
    if not raw_name:
        return raw_name
    round_resume_match = re.match(r"^(.*)_round\d+_resume$", raw_name)
    if round_resume_match and round_resume_match.group(1):
        return round_resume_match.group(1)
    for suffix in (
        "_baseline_compare",
        "_bootstrap_auto",
        "_bootstrap_custom_maker",
        "_from_template",
        "_lock_validation",
        "_manual_g1center_test",
        "_validation",
    ):
        if raw_name.endswith(suffix) and len(raw_name) > len(suffix):
            return raw_name[: -len(suffix)]
    return raw_name


def _camera_name_from_output_dir(output_dir: Path) -> str:
    root = _default_sim_output_root()
    try:
        relative_parts = output_dir.resolve().relative_to(root.resolve()).parts
    except Exception:
        return _canonical_camera_group_name(output_dir.name)
    if relative_parts:
        return _canonical_camera_group_name(relative_parts[0])
    return _canonical_camera_group_name(output_dir.name)


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


def _build_camera_history_summary(camera_name: str) -> dict:
    history_dirs = _iter_camera_history_dirs(camera_name)
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


def _write_camera_history_summary(camera_name: str) -> Tuple[Path, dict]:
    summary = _build_camera_history_summary(camera_name)
    summary_path = _camera_history_summary_path(camera_name)
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


def _write_camera_history_summary_compact(camera_name: str, summary: dict) -> Path:
    compact_summary = _build_camera_history_summary_compact(summary)
    compact_summary_path = _camera_history_summary_compact_path(camera_name)
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


def _camera_scope_output_dir(output_dir: Path) -> Path:
    """Return the camera-scoped root directory under SimOutput for an output path."""
    root = _default_sim_output_root()
    try:
        relative = output_dir.relative_to(root)
    except Exception:
        return output_dir
    if not relative.parts:
        return output_dir
    return root / _canonical_camera_group_name(relative.parts[0])


def _marker_path_for_output_dir(output_dir: Path) -> Path:
    """Return marker path for an output_dir.

    Prefer placing the marker inside the camera-scoped directory under
    SimOutput, otherwise fall back to the output_dir itself.
    """
    camera_scope_dir = _camera_scope_output_dir(output_dir)
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


def _load_history_best_run_for_config(
    config_path: Path,
    camera_name: Optional[str] = None,
) -> Optional[dict]:
    cfg_payload = _load_json_if_exists(config_path)
    required_score_scope = _resolve_score_scope_from_cfg(cfg_payload)
    cfg_board_signature = _compute_board_signature(cfg_payload.get("boards")) if isinstance(cfg_payload, dict) else None
    config_camera_name = _camera_name_from_config_path(config_path)
    history_camera_name = _canonical_camera_group_name(config_camera_name)
    if not history_camera_name and camera_name:
        history_camera_name = _canonical_camera_group_name(camera_name)
    if not history_camera_name:
        print(f"[history_best] No history camera name for config={config_path}, camera_name={camera_name}")
        return None
    history_dirs = _iter_camera_history_dirs(history_camera_name)
    print(f"[history_best] camera={history_camera_name}, cfg_scope={required_score_scope}, cfg_sig={cfg_board_signature}, dirs={history_dirs}")
    best_run: Optional[dict] = None
    total_results = 0
    filtered_scope = 0
    filtered_sig = 0
    filtered_digest = 0
    for history_dir in history_dirs:
        for result_path in sorted(history_dir.rglob("result.json")):
            payload = _load_json_if_exists(result_path)
            if not isinstance(payload, dict):
                continue
            digest = _build_run_digest_from_result_payload(
                payload,
                result_path,
                include_in_progress=True,
            )
            if digest is None:
                filtered_digest += 1
                continue
            if required_score_scope is not None:
                digest_score_scope = _resolve_score_scope_from_payload(digest)
                if digest_score_scope != required_score_scope:
                    filtered_scope += 1
                    continue
            if cfg_board_signature is not None:
                result_signature = _compute_board_signature(payload.get("boards"))
                if result_signature is None or result_signature != cfg_board_signature:
                    filtered_sig += 1
                    print(f"[history_best] sig mismatch: {result_path.parent.name} result_sig={result_signature} != cfg_sig={cfg_board_signature}")
                    continue
            total_results += 1
            if best_run is None or float(digest.get("final_score", float("inf"))) < float(
                best_run.get("final_score", float("inf"))
            ):
                best_run = digest
    print(f"[history_best] total_pass={total_results}, filtered: digest={filtered_digest} scope={filtered_scope} sig={filtered_sig}")
    if isinstance(best_run, dict):
        print(f"[history_best] FOUND: score={best_run.get('final_score')}, src={best_run.get('result_path')}")
        return dict(best_run)
    print(f"[history_best] NOT FOUND for camera={history_camera_name}")
    return None


def _load_history_best_run_any_boards(
    config_path: Path,
    camera_name: Optional[str] = None,
) -> Optional[dict]:
    cfg_payload = _load_json_if_exists(config_path)
    required_score_scope = _resolve_score_scope_from_cfg(cfg_payload)
    config_camera_name = _camera_name_from_config_path(config_path)
    history_camera_name = _canonical_camera_group_name(config_camera_name)
    if not history_camera_name and camera_name:
        history_camera_name = _canonical_camera_group_name(camera_name)
    if not history_camera_name:
        return None
    history_dirs = _iter_camera_history_dirs(history_camera_name)
    best_entry: Optional[dict] = None
    best_score: Optional[float] = None
    for history_dir in history_dirs:
        for result_path in sorted(history_dir.rglob("result.json")):
            payload = _load_json_if_exists(result_path)
            if not isinstance(payload, dict):
                continue
            digest = _build_run_digest_from_result_payload(
                payload,
                result_path,
                include_in_progress=True,
            )
            if digest is None:
                continue
            if required_score_scope is not None:
                digest_score_scope = _resolve_score_scope_from_payload(digest)
                if digest_score_scope != required_score_scope:
                    continue
            final_score = digest.get("final_score")
            if final_score is None:
                continue
            try:
                final_score = float(final_score)
            except (TypeError, ValueError):
                continue
            if best_score is not None and final_score >= best_score:
                continue
            best_score = final_score
            best_entry = digest
    if isinstance(best_entry, dict):
        print(f"[history_any_boards] FOUND: score={best_entry.get('final_score')}, src={best_entry.get('result_path')}")
        return dict(best_entry)
    return None


def _evaluate_seed_candidate(
    config_path: Path,
    cfg: dict,
    candidate_values: Dict[str, float],
    camera_name: str,
) -> Optional[float]:
    try:
        temp_cfg = _cfg_with_initial_values(copy.deepcopy(cfg), candidate_values)
        calib = CameraCalibrator(temp_cfg, config_path=config_path)
        score = calib._apply_initial_value_map_with_retry()
        return float(score)
    except Exception as exc:
        print(f"[seed_eval] Failed to evaluate candidate: {exc}")
        return None


def _compare_and_pick_better_seed(
    config_path: Path,
    cfg: dict,
    camera_name: str,
    matched_values: Dict[str, float],
    matched_score: Optional[float],
    any_board_values: Dict[str, float],
    any_board_score: Optional[float],
    base_output_dir: Optional[Path] = None,
) -> Tuple[Dict[str, float], Optional[float], str]:
    # Seed cache: skip re-evaluation if candidates haven't changed
    if base_output_dir is not None:
        seed_cache_path = base_output_dir / "seed_cache.json"
        cache_key = json.dumps(
            {"matched": matched_values, "any_board": any_board_values}, sort_keys=True
        )
        if seed_cache_path.exists():
            try:
                cached = json.loads(seed_cache_path.read_text(encoding="utf-8"))
                if cached.get("cache_key") == cache_key:
                    winner = cached["winner"]
                    print(f"[seed_cache] HIT: using cached winner={winner}")
                    if winner == "any_board":
                        return any_board_values, any_board_score, "any_board"
                    return matched_values, matched_score, "matched"
            except Exception:
                pass
    print(f"[seed_compare] Evaluating matched candidate (history score={matched_score})...")
    score_a = _evaluate_seed_candidate(config_path, cfg, matched_values, camera_name)
    print(f"[seed_compare] Evaluating any-board candidate (history score={any_board_score})...")
    score_b = _evaluate_seed_candidate(config_path, cfg, any_board_values, camera_name)

    if score_a is None and score_b is None:
        winner = "matched"
    elif score_a is None:
        winner = "any_board"
    elif score_b is None:
        winner = "matched"
    elif score_b < score_a:
        print(f"[seed_compare] any-board candidate wins: {score_b:.1f} < {score_a:.1f}")
        winner = "any_board"
    else:
        print(f"[seed_compare] matched candidate wins: {score_a:.1f} <= {score_b:.1f}")
        winner = "matched"

    # Write cache
    if base_output_dir is not None:
        base_output_dir.mkdir(parents=True, exist_ok=True)
        seed_cache_path.write_text(
            json.dumps({"cache_key": cache_key, "winner": winner}, ensure_ascii=False),
            encoding="utf-8",
        )
    if winner == "any_board":
        return any_board_values, any_board_score, "any_board"
    return matched_values, matched_score, "matched"




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


def _read_vehicle_initial_values_via_dde(camera_name: str) -> Optional[Dict[str, float]]:
    runtime_context = _probe_runtime_vehicle_context()
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
    max_retries: int = 3,
    retry_delay_sec: float = 2.0,
) -> Dict[str, float]:
    for attempt in range(1, max_retries + 1):
        values = _read_vehicle_initial_values_via_dde(camera_name)
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


def _probe_runtime_vehicle_context() -> Optional[dict]:
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

    project_root = Path(__file__).resolve().parents[3]
    vehicle_path = project_root / "Data" / "Vehicle" / Path(vehicle_key.replace("\\", "/"))
    return {
        "project_root": project_root,
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
    if payload.get("enabled") is False:
        _VEHICLE_WRITEBACK_CONTEXT_CACHE[cache_key] = None
        return None

    project_root = Path(payload.get("project_root", Path(__file__).resolve().parents[3]))
    vehicle_key = str(payload.get("vehicle", payload.get("vehicle_key", ""))).strip()
    vehicle_path: Optional[Path] = None
    testrun_name = str(payload.get("testrun", "")).strip() or None
    sensor_name = str(payload.get("sensor_name", "")).strip() or None

    if vehicle_key:
        candidate = Path(vehicle_key.replace("\\", "/"))
        if candidate.is_absolute():
            vehicle_path = candidate
        else:
            parts = list(candidate.parts)
            if len(parts) >= 2 and parts[0].lower() == "data" and parts[1].lower() == "vehicle":
                candidate = Path(*parts[2:])
            vehicle_path = project_root / "Data" / "Vehicle" / candidate
    else:
        runtime_context = _probe_runtime_vehicle_context()
        if runtime_context is not None:
            project_root = Path(runtime_context.get("project_root") or project_root)
            vehicle_key = str(runtime_context.get("vehicle_key") or "").strip()
            vehicle_path = Path(runtime_context.get("vehicle_path")) if runtime_context.get("vehicle_path") else None
            testrun_name = testrun_name or runtime_context.get("testrun")

    if vehicle_path is None:
        print(f"Skipped vehicle writeback: unable to resolve vehicle path for {config_path}")
        _VEHICLE_WRITEBACK_CONTEXT_CACHE[cache_key] = None
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
) -> Optional[dict]:
    context = _resolve_vehicle_writeback_context(config_path, cfg)
    if context is None:
        return None

    vehicle_path = Path(context["vehicle_path"])
    if not vehicle_path.exists():
        print(f"Skipped vehicle writeback: vehicle file not found at {vehicle_path}")
        return None

    history_best_run = _load_history_best_run_for_config(config_path, camera_name)
    if isinstance(history_best_run, dict):
        try:
            history_best_score = float(history_best_run.get("final_score"))
            if float(best_score) > history_best_score + 1e-6:
                print(
                    f"Skipped vehicle writeback: current score {float(best_score):.2f} "
                    f"worse than history best {history_best_score:.2f} "
                    f"(camera={camera_name}, vehicle={vehicle_path})"
                )
                return None
        except Exception:
            pass

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




def _clamp_to_parameter_bounds(param_cfg: dict, value: float, initial_value: Optional[float] = None) -> float:
    min_value, max_value = _resolve_parameter_bounds(param_cfg, initial_value=initial_value)
    decimals = int(param_cfg.get("decimals", 4))
    min_value = round(min_value, decimals)
    max_value = round(max_value, decimals)
    return max(min_value, min(max_value, round(value, decimals)))


def _quantize_float(value: float, decimals: int) -> float:
    return float(f"{float(value):.{decimals}f}")


def _format_scalar_value_map(values: Dict[str, float]) -> str:
    ordered = []
    for name in sorted(values.keys()):
        ordered.append(f"{name}={values[name]}")
    return ", ".join(ordered)


_DEFAULT_BOUNDS_MULTIPLIER = 50.0


def _resolve_parameter_bounds(param_cfg: dict, initial_value: Optional[float] = None) -> Tuple[float, float]:
    effective_initial = float(initial_value) if initial_value is not None else float(param_cfg.get("initial", 0.0))
    bounds_multiplier = float(param_cfg.get("bounds_multiplier", _DEFAULT_BOUNDS_MULTIPLIER))
    step = float(param_cfg.get("step", 0.001))
    half_range = step * bounds_multiplier
    return effective_initial - half_range, effective_initial + half_range


def _build_explicit_parameter_config(param_cfg: dict, initial_value: float) -> dict:
    min_value, max_value = _resolve_parameter_bounds(param_cfg, initial_value=initial_value)
    decimals = int(param_cfg.get("decimals", 4))
    min_value = round(min_value, decimals)
    max_value = round(max_value, decimals)
    quantized_initial = _clamp_to_parameter_bounds(param_cfg, initial_value, initial_value=initial_value)

    explicit_param_cfg = copy.deepcopy(param_cfg)
    explicit_param_cfg["initial"] = quantized_initial
    explicit_param_cfg["min"] = min_value
    explicit_param_cfg["max"] = max_value
    return explicit_param_cfg


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


def _build_multi_start_run_configs(
    cfg: dict,
    base_output_dir: Path,
    start_count: int,
    jitter_steps: float,
    seed: int,
    max_iters_override: Optional[int],
    output_root_dir: Optional[Path] = None,
) -> Tuple[Path, List[dict]]:
    if start_count <= 0:
        raise ValueError("multi-start count must be positive")

    base_parameters = cfg.get("parameters")
    if not isinstance(base_parameters, dict) or not base_parameters:
        raise ValueError("parameters must be a non-empty object for multi-start mode")

    root_output_dir = output_root_dir or _build_isolated_output_dir(
        "multistart", camera_parent=base_output_dir.name
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
        coarse_start_multipliers = [max(1.0, float(jitter_steps) or 1.0)]
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
        run_output_dir = root_output_dir / f"start_{start_index:02d}"
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
                if jitter_steps > 0.0:
                    delta += (
                        rng.uniform(-jitter_steps, jitter_steps)
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


def _run_multi_start_campaign(
    config_path: Path,
    cfg: dict,
    base_output_dir: Path,
    start_count: int,
    jitter_steps: float,
    seed: int,
    max_iters_override: Optional[int],
    output_root_dir: Optional[Path] = None,
    *,
    round_index: int = 0,
    round_count: int = 0,
    overall_total_iters: int = 0,
    ) -> dict:
    root_output_dir, run_cfgs = _build_multi_start_run_configs(
        cfg,
        base_output_dir=base_output_dir,
        start_count=start_count,
        jitter_steps=jitter_steps,
        seed=seed,
        max_iters_override=max_iters_override,
        output_root_dir=output_root_dir,
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
        live_log_path = _configure_live_log(run_cfg, False)
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
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

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
    anchor_values = _extract_initial_values_from_cfg(cfg)
    anchor_score: Optional[float] = None
    anchor_source = "config_initial"

    # If config has initial values (from vehicle file), use them as authoritative source
    # Only fall back to history_best if config doesn't have initial values
    if anchor_values:
        print(f"Using config initial values as seed anchor (from vehicle file)")
        return anchor_values, anchor_score, anchor_source

    if not bool(policy.get("prefer_history_best", True)):
        return anchor_values, anchor_score, anchor_source

    history_best_run = _load_history_best_run_for_config(config_path, camera_name)
    if not isinstance(history_best_run, dict):
        return anchor_values, anchor_score, anchor_source

    history_values = history_best_run.get("final_values")
    if isinstance(history_values, dict) and history_values:
        anchor_values = {
            name: float(value)
            for name, value in history_values.items()
            if isinstance(value, (int, float))
        }
        parameters = cfg.get("parameters", {})
        for name in list(anchor_values.keys()):
            param_cfg = parameters.get(name)
            if param_cfg is None:
                continue
            raw = anchor_values[name]
            clamped = _clamp_to_parameter_bounds(param_cfg, raw)
            if abs(clamped - raw) > 1e-9:
                print(f"Warning: clamping history {name}={raw} to {clamped} (out of current config range)")
            anchor_values[name] = clamped
        anchor_source = "history_best"

    try:
        anchor_score = float(history_best_run.get("final_score"))
    except Exception:
        anchor_score = None
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
    start_count: int,
    jitter_steps: float,
    seed: int,
    explore_max_iters: int,
    refine_max_iters: Optional[int],
    previous_escape_stagnation_rounds: int = 0,
    anchor_score: Optional[float] = None,
    output_root_dir: Optional[Path] = None,
    *,
    round_index: int = 0,
    round_count: int = 0,
    overall_total_iters: int = 0,
    ) -> dict:
    if start_count <= 0:
        raise ValueError("explore-then-refine mode requires a positive start count")
    if explore_max_iters <= 0:
        raise ValueError("explore-then-refine mode requires positive explore iterations")

    campaign_root = output_root_dir or _build_isolated_output_dir(
        "campaign", camera_parent=base_output_dir.name
    )
    campaign_root.mkdir(parents=True, exist_ok=True)

    print(
        "Explore-then-refine campaign: "
        f"explore_runs={start_count}, "
        f"explore_iters={explore_max_iters}, "
        f"refine_iters={refine_max_iters or int(cfg.get('max_iters', 0))}, "
        f"jitter_steps={jitter_steps}, "
        f"seed={seed}, "
        f"campaign_dir={campaign_root}"
    )

    explore_summary = _run_multi_start_campaign(
        config_path=config_path,
        cfg=cfg,
        base_output_dir=base_output_dir,
        start_count=start_count,
        jitter_steps=jitter_steps,
        seed=seed,
        max_iters_override=explore_max_iters,
        output_root_dir=campaign_root / "explore",
        round_index=round_index,
        round_count=round_count,
        overall_total_iters=overall_total_iters,
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
            "campaign_output_dir": str(campaign_root),
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
        summary_path = campaign_root / "campaign_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
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
    refine_output_dir = campaign_root / "refine"
    refine_cfg["output_dir"] = str(refine_output_dir)
    _set_run_local_script_control_result_path(refine_cfg, refine_output_dir)
    if refine_max_iters is not None:
        refine_cfg["max_iters"] = int(refine_max_iters)

    live_log_path = _configure_live_log(refine_cfg, False)
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
        "campaign_output_dir": str(campaign_root),
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
    summary_path = campaign_root / "campaign_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

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
    *,
    resume_from_result: bool,
    output_dir_override: Optional[Path] = None,
    round_index: int = 0,
    ) -> dict:
    run_cfg = copy.deepcopy(cfg)
    marker_path = _marker_path_for_output_dir(base_output_dir)
    resume_result_path: Optional[Path] = None
    if resume_from_result:
        resume_result_path = _read_latest_result_path(marker_path, base_output_dir)

    if output_dir_override is not None:
        run_cfg["output_dir"] = str(output_dir_override)
    else:
        run_cfg["output_dir"] = str(
            _build_isolated_output_dir("run", camera_parent=base_output_dir.name)
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
    *,
    resume_from_result: bool,
) -> dict:
    if round_count <= 0:
        raise ValueError("round_count must be positive")

    rounds_root = _build_isolated_output_dir("rounds", camera_parent=base_output_dir.name)
    active_cfg = copy.deepcopy(cfg)
    target_score = float(cfg.get("target_score", 5.0))
    round_seed_policy = _resolve_round_seed_policy(cfg)
    anchor_values, anchor_score, anchor_source = _resolve_round_seed_anchor(
        config_path,
        camera_name,
        cfg,
        round_seed_policy,
    )
    any_board_run = _load_history_best_run_any_boards(config_path, camera_name)
    if isinstance(any_board_run, dict):
        any_board_values_raw = any_board_run.get("final_values", {})
        any_board_values = {
            name: float(value)
            for name, value in any_board_values_raw.items()
            if isinstance(value, (int, float))
        }
        parameters = cfg.get("parameters", {})
        for name in list(any_board_values.keys()):
            param_cfg = parameters.get(name)
            if param_cfg is not None:
                any_board_values[name] = _clamp_to_parameter_bounds(param_cfg, any_board_values[name])
        any_board_score_val = None
        try:
            any_board_score_val = float(any_board_run.get("final_score"))
        except Exception:
            pass
        anchor_values, anchor_score, anchor_source = _compare_and_pick_better_seed(
            config_path, cfg, camera_name,
            anchor_values, anchor_score,
            any_board_values, any_board_score_val,
            base_output_dir=base_output_dir,
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
        round_output_dir = rounds_root / f"round_{round_no:02d}" / "run"
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


def _run_multi_start_rounds(
    config_path: Path,
    cfg: dict,
    base_output_dir: Path,
    camera_name: str,
    round_count: int,
    start_count: int,
    jitter_steps: float,
    seed: int,
    max_iters_override: Optional[int],
) -> dict:
    if round_count <= 0:
        raise ValueError("round_count must be positive")
    _run_max_iters = max_iters_override or int(cfg.get("max_iters", 0))
    overall_total_iters = round_count * start_count * _run_max_iters

    rounds_root = _build_isolated_output_dir("rounds", camera_parent=base_output_dir.name)
    active_cfg = copy.deepcopy(cfg)
    target_score = float(cfg.get("target_score", 5.0))
    round_seed_policy = _resolve_round_seed_policy(cfg)
    anchor_values, anchor_score, anchor_source = _resolve_round_seed_anchor(
        config_path,
        camera_name,
        cfg,
        round_seed_policy,
    )
    any_board_run = _load_history_best_run_any_boards(config_path, camera_name)
    if isinstance(any_board_run, dict):
        any_board_values_raw = any_board_run.get("final_values", {})
        any_board_values = {
            name: float(value)
            for name, value in any_board_values_raw.items()
            if isinstance(value, (int, float))
        }
        parameters = cfg.get("parameters", {})
        for name in list(any_board_values.keys()):
            param_cfg = parameters.get(name)
            if param_cfg is not None:
                any_board_values[name] = _clamp_to_parameter_bounds(param_cfg, any_board_values[name])
        any_board_score_val = None
        try:
            any_board_score_val = float(any_board_run.get("final_score"))
        except Exception:
            pass
        anchor_values, anchor_score, anchor_source = _compare_and_pick_better_seed(
            config_path, cfg, camera_name,
            anchor_values, anchor_score,
            any_board_values, any_board_score_val,
            base_output_dir=base_output_dir,
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
        round_output_dir = rounds_root / f"round_{round_no:02d}" / "multistart"
        round_seed = int(seed) + round_index
        print(
            f"Multi-start rounds: round={round_no}/{round_count} "
            f"seed={round_seed} output_dir={round_output_dir}"
        )
        summary = _run_multi_start_campaign(
            config_path=config_path,
            cfg=active_cfg,
            base_output_dir=base_output_dir,
            start_count=start_count,
            jitter_steps=jitter_steps,
            seed=round_seed,
            max_iters_override=max_iters_override,
            output_root_dir=round_output_dir,
            round_index=round_index + 1,
            round_count=round_count,
            overall_total_iters=overall_total_iters,
        )
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
            "output_dir": summary["output_dir"],
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
                f"Multi-start rounds: stop early at round {round_no} because target_score was reached"
            )
            break

    if best_round is not None:
        _write_best_values_to_vehicle_config(
            config_path,
            active_cfg,
            camera_name,
            float(best_round["best_run"]["best_score"]),
            best_round["best_run"]["best_values"],
        )

    payload = {
        "mode": "multi-start-rounds",
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
) -> dict:
    if round_count <= 0:
        raise ValueError("round_count must be positive")

    _refine_max_iters = refine_max_iters or int(cfg.get("max_iters", 0))
    overall_total_iters = round_count * (start_count * explore_max_iters + _refine_max_iters)
    rounds_root = _build_isolated_output_dir("rounds", camera_parent=base_output_dir.name)
    active_cfg = copy.deepcopy(cfg)
    target_score = float(cfg.get("target_score", 5.0))
    round_seed_policy = _resolve_round_seed_policy(cfg)
    anchor_values, anchor_score, anchor_source = _resolve_round_seed_anchor(
        config_path,
        camera_name,
        cfg,
        round_seed_policy,
    )
    any_board_run = _load_history_best_run_any_boards(config_path, camera_name)
    if isinstance(any_board_run, dict):
        any_board_values_raw = any_board_run.get("final_values", {})
        any_board_values = {
            name: float(value)
            for name, value in any_board_values_raw.items()
            if isinstance(value, (int, float))
        }
        parameters = cfg.get("parameters", {})
        for name in list(any_board_values.keys()):
            param_cfg = parameters.get(name)
            if param_cfg is not None:
                any_board_values[name] = _clamp_to_parameter_bounds(param_cfg, any_board_values[name])
        any_board_score_val = None
        try:
            any_board_score_val = float(any_board_run.get("final_score"))
        except Exception:
            pass
        anchor_values, anchor_score, anchor_source = _compare_and_pick_better_seed(
            config_path, cfg, camera_name,
            anchor_values, anchor_score,
            any_board_values, any_board_score_val,
            base_output_dir=base_output_dir,
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
        round_output_dir = rounds_root / f"round_{round_no:02d}" / "campaign"
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


@dataclass
class ParameterSpec:
    name: str
    value: float
    step: float
    min_value: float
    max_value: float
    min_step: float
    decimals: int


@dataclass
class BoardProfile:
    board_id: str
    board_type: str
    weight: float
    critical: bool
    roi: Optional[Tuple[int, int, int, int]]
    detect_roi_padding: int = 0
    template_source_roi: Optional[Tuple[int, int, int, int]] = None
    template_source_crop: Optional[Tuple[int, int, int, int]] = None
    board_size: Optional[Tuple[int, int]] = None
    square_size: float = 1.0
    alpha: float = 1000.0
    beta: float = 0.1
    fail_penalty: float = 1e6
    min_detected_points: int = 1
    degrade_threshold_rmse: float = float("inf")
    degrade_threshold_max_error: float = float("inf")
    degrade_threshold_miss_rate: float = float("inf")
    template_image: Optional[str] = None
    min_match_count: int = 20
    custom_detector: str = "feature"
    template_match_threshold: float = 0.0
    template_binary_threshold: int = 0
    template_crop: Optional[Tuple[int, int, int, int]] = None
    aruco_dictionary: str = "DICT_4X4_50"
    marker_length_ratio: float = 0.7
    tag_family: str = "tagStandard41h12"


@dataclass
class DetectionResult:
    board_id: str
    success: bool
    point_count: int
    ordered_points: np.ndarray
    board_type: str
    roi_used: Optional[Tuple[int, int, int, int]] = None
    detector: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class BoardScoreDetail:
    board_id: str
    board_type: str
    success: bool
    compared: bool
    reference_visible: bool
    sim_visible: bool
    total_score: float
    rmse: float
    mean_error: float
    max_error: float
    miss_rate: float
    matched_point_count: int
    failed_reason: Optional[str] = None


@dataclass
class TotalScoreDetail:
    success: bool
    total_score: float
    raw_total_score: float
    degrade_penalty: float
    has_critical_degrade: bool
    degraded_boards: List[str]
    isolated_outlier_boards: List[str]
    compared_board_count: int
    board_scores: List[BoardScoreDetail]
    failed_reason: Optional[str] = None


def _build_annotation_legend_lines(total_detail: TotalScoreDetail) -> List[str]:
    isolated_outlier_board_set = set(total_detail.isolated_outlier_boards)
    lines: List[str] = []
    for score in total_detail.board_scores:
        if score.compared:
            line = f"{score.board_id}: {score.total_score:.3f}"
            if score.board_id in isolated_outlier_board_set:
                line += " (excluded)"
        else:
            line = f"{score.board_id}: skipped"
        lines.append(line)
    return lines


@dataclass
class AcceptanceDecision:
    passed: bool
    mode: str
    reason: str
    target_score_reached: bool
    compared_board_count: int
    max_board_score: Optional[float]
    avg_board_score: Optional[float]


@dataclass
class EvalImageTransform:
    scale_x: float
    scale_y: float
    offset_x: int
    offset_y: int
    eval_width: int
    eval_height: int
    source_width: int
    source_height: int


class CameraCalibrator:
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
        self.repo_root = Path(__file__).resolve().parents[3]
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
        self.max_history_entries = max(0, int(cfg.get("max_history_entries", 500)))
        self.optimizer_mode = str(cfg.get("optimizer_mode", "coordinate_descent")).lower()
        if self.optimizer_mode not in {"coordinate_descent", "bayesian", "auto"}:
            raise ValueError("optimizer_mode must be 'coordinate_descent', 'bayesian', or 'auto'")
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
            }:
                raise ValueError(f"Unsupported board_type for {board_id}: {board_type}")

            roi = self._parse_roi(board.get("roi"))
            template_source_roi = self._parse_roi(board.get("template_source_roi"))
            template_source_crop = self._parse_roi(board.get("template_source_crop"))
            board_size = None
            if board_type in {"checkerboard", "charuco"}:
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
    def _preprocess_template_match_image(
        gray_image: np.ndarray, board: BoardProfile
    ) -> np.ndarray:
        if board.template_binary_threshold > 0:
            _, processed = cv2.threshold(
                gray_image,
                float(board.template_binary_threshold),
                255,
                cv2.THRESH_BINARY_INV,
            )
            return processed.astype(np.uint8)
        return gray_image

    @staticmethod
    def _points_bbox(points: np.ndarray) -> Tuple[int, int, int, int]:
        reshaped = points.reshape(-1, 2)
        min_x = int(math.floor(float(np.min(reshaped[:, 0]))))
        min_y = int(math.floor(float(np.min(reshaped[:, 1]))))
        max_x = int(math.ceil(float(np.max(reshaped[:, 0]))))
        max_y = int(math.ceil(float(np.max(reshaped[:, 1]))))
        return min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y)

    @staticmethod
    def _expand_bbox(
        bbox: Tuple[int, int, int, int],
        image_shape: Tuple[int, int],
        ratio: float = 0.18,
        min_pad: int = 24,
    ) -> Tuple[int, int, int, int]:
        x, y, width, height = bbox
        img_h, img_w = image_shape[:2]
        pad_x = max(min_pad, int(round(width * ratio)))
        pad_y = max(min_pad, int(round(height * ratio)))
        x0 = max(0, x - pad_x)
        y0 = max(0, y - pad_y)
        x1 = min(img_w, x + width + pad_x)
        y1 = min(img_h, y + height + pad_y)
        return x0, y0, max(1, x1 - x0), max(1, y1 - y0)

    @staticmethod
    def _bbox_iou(
        lhs: Tuple[int, int, int, int], rhs: Tuple[int, int, int, int]
    ) -> float:
        lx, ly, lw, lh = lhs
        rx, ry, rw, rh = rhs
        left = max(lx, rx)
        top = max(ly, ry)
        right = min(lx + lw, rx + rw)
        bottom = min(ly + lh, ry + rh)
        if left >= right or top >= bottom:
            return 0.0
        intersection = float((right - left) * (bottom - top))
        union = float(lw * lh + rw * rh) - intersection
        if union <= 0.0:
            return 0.0
        return intersection / union

    @staticmethod
    def _bbox_center(bbox: Tuple[int, int, int, int]) -> Tuple[float, float]:
        x, y, width, height = bbox
        return x + (width * 0.5), y + (height * 0.5)

    def _custom_board_content_geometry(self, board: BoardProfile) -> Tuple[float, float, float, float]:
        template_info = self.custom_templates.get(board.board_id, {})
        content_bbox = template_info.get("content_bbox")
        if content_bbox is not None:
            offset_x, offset_y, width, height = content_bbox
            return float(offset_x), float(offset_y), float(width), float(height)

        reference_bbox = board.template_source_roi or board.roi
        if reference_bbox is None:
            return 0.0, 0.0, 1.0, 1.0
        return 0.0, 0.0, float(reference_bbox[2]), float(reference_bbox[3])

    @staticmethod
    def _anchors_from_bbox(bbox: Tuple[int, int, int, int]) -> np.ndarray:
        x, y, width, height = bbox
        anchor_x = float(x)
        anchor_y = float(y)
        anchor_w = float(width)
        anchor_h = float(height)
        return np.array(
            [
                [anchor_x, anchor_y],
                [anchor_x + anchor_w - 1.0, anchor_y],
                [anchor_x + anchor_w - 1.0, anchor_y + anchor_h - 1.0],
                [anchor_x, anchor_y + anchor_h - 1.0],
                [anchor_x + anchor_w * 0.5, anchor_y + anchor_h * 0.5],
                [anchor_x + anchor_w * 0.25, anchor_y + anchor_h * 0.25],
                [anchor_x + anchor_w * 0.75, anchor_y + anchor_h * 0.25],
                [anchor_x + anchor_w * 0.75, anchor_y + anchor_h * 0.75],
                [anchor_x + anchor_w * 0.25, anchor_y + anchor_h * 0.75],
            ],
            dtype=np.float32,
        )

    @staticmethod
    def _checkerboard_outline(
        points: np.ndarray,
        board_size: Optional[Tuple[int, int]],
    ) -> Optional[np.ndarray]:
        if board_size is None:
            return None
        cols, rows = int(board_size[0]), int(board_size[1])
        reshaped = points.reshape(-1, 2).astype(np.float32)
        if reshaped.shape[0] != cols * rows or cols < 2 or rows < 2:
            return None
        grid = reshaped.reshape(rows, cols, 2)

        top_left = grid[0, 0]
        top_right = grid[0, -1]
        bottom_right = grid[-1, -1]
        bottom_left = grid[-1, 0]

        top_step = grid[0, 1] - grid[0, 0]
        right_step = grid[1, -1] - grid[0, -1]
        bottom_step = grid[-1, -1] - grid[-1, -2]
        left_step = grid[-1, 0] - grid[-2, 0]

        outline = np.array(
            [
                top_left - top_step - (grid[1, 0] - grid[0, 0]),
                top_right + (grid[0, -1] - grid[0, -2]) - right_step,
                bottom_right + bottom_step + (grid[-1, -1] - grid[-2, -1]),
                bottom_left - (grid[-1, 1] - grid[-1, 0]) + left_step,
            ],
            dtype=np.float32,
        )
        return outline

    def _reference_detection_from_board_geometry(
        self,
        board: BoardProfile,
    ) -> Optional[DetectionResult]:
        if not _is_custom_marker_board_type(board.board_type):
            return None
        reference_bbox = board.template_source_roi or board.roi
        if reference_bbox is None:
            return None
        offset_x, offset_y, width, height = self._custom_board_content_geometry(board)
        reference_bbox = (
            int(round(reference_bbox[0] + offset_x)),
            int(round(reference_bbox[1] + offset_y)),
            max(1, int(round(width))),
            max(1, int(round(height))),
        )
        anchors = self._anchors_from_bbox(reference_bbox)
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(anchors.shape[0]),
            ordered_points=anchors,
            board_type=board.board_type,
            roi_used=reference_bbox,
            detector="reference_roi",
        )

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

    def preflight_script_control(self) -> None:
        self.script_control_template_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_control_script_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_control_result_path.parent.mkdir(parents=True, exist_ok=True)
        print(
            "Script Control preflight: "
            f"template_path={self.script_control_template_path}, "
            f"runtime_path={self.script_control_script_path}, "
            f"result_path={self.script_control_result_path}, "
            f"dde_service={self.script_control_dde_service}, "
            f"dde_topic={self.script_control_dde_topic}"
        )

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
                'update',
                'if {![winfo exists .camera.btn.set]} {error "missing widget .camera.btn.set"}',
                '.camera.btn.set invoke',
                'update',
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

    def _record_dde_operation_success(self) -> None:
        if self._dde_recovery_probe_active:
            return
        self._reset_dde_dispatch_circuit()

    def _record_dde_operation_failure(self, exc: BaseException, operation: str) -> None:
        if self._dde_recovery_probe_active:
            return
        if not self._runtime_error_needs_dde_recovery_probe(exc):
            self._reset_dde_dispatch_circuit()
            return

        self.dde_dispatch_failure_streak += 1
        self.dde_circuit_last_error_text = self._summarize_dde_detail(exc)
        if self.dde_dispatch_failure_streak < self.dde_circuit_trip_failures:
            return
        if self.dde_circuit_opened_at is None:
            self.dde_circuit_opened_at = time.perf_counter()
            self._log_dde_retry_event(
                "dde_dispatch_circuit",
                self.dde_dispatch_failure_streak,
                self.dde_circuit_trip_failures,
                "opened",
                0.0,
                detail=f"operation={operation} error={self.dde_circuit_last_error_text}",
            )

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
            import cmapi_testrun_control as cmctrl

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

    def _apply_value_map_or_recover(self, values: Dict[str, float], context: str) -> None:
        try:
            self._apply_value_map(values)
        except RuntimeError as exc:
            restored = self._recover_after_runtime_error(values, exc)
            if restored:
                return
            raise RuntimeError(f"{context}: {exc}") from exc

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

    def _apply_script_control_params(self, params: List[ParameterSpec]) -> None:
        if not params:
            return

        for param in params:
            param.value = self._quantize_param_value(param, param.value)

        last_error: Optional[RuntimeError] = None
        for attempt in range(3):
            msg = self._run_script_control_script(self._render_script_control_apply_script(params))
            observed: Dict[str, float] = {}
            for line in msg.splitlines():
                if "=" not in line:
                    continue
                name, raw_value = line.split("=", 1)
                try:
                    observed[name.strip()] = float(raw_value.replace(",", ".").strip())
                except ValueError:
                    continue

            mismatches: List[str] = []
            for param in params:
                expected = self._quantize_param_value(param, param.value)
                actual = observed.get(param.name)
                read_decimals = self.SCRIPT_CONTROL_READ_DECIMALS.get(param.name, param.decimals)
                if not self._script_control_readback_matches(
                    expected,
                    actual,
                    param.decimals,
                    read_decimals,
                ):
                    expected_readback = self._quantize_value(expected, read_decimals)
                    mismatches.append(
                        f"{param.name}: expected {expected_readback}, read back {actual}"
                    )

            if not mismatches:
                time.sleep(self.script_control_settle_sec)
                return

            last_error = RuntimeError(
                "Script Control verification failed after apply attempt "
                f"{attempt + 1}: " + "; ".join(mismatches)
            )
            time.sleep(0.1)

        if last_error is not None:
            raise last_error

        time.sleep(self.script_control_settle_sec)

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

    def _preflight_capture_aspect_ratio(self) -> None:
        try:
            raw_w, raw_h = self._get_movie_dde_view_size()
        except RuntimeError as exc:
            print(f"Capture aspect preflight skipped: {exc}")
            return
        ref_h, ref_w = self.real_img.shape[:2]

        if raw_w * ref_h != ref_w * raw_h:
            print(
                "WARNING: Current movie capture aspect ratio does not match real_image: "
                f"captured={raw_w}x{raw_h}, real={ref_w}x{ref_h}"
            )
            return
        print(
            "Capture aspect preflight: "
            f"raw={raw_w}x{raw_h}, real={ref_w}x{ref_h}"
        )

    def _capture_movie_via_dde(self, tag: str) -> Path:
        out_path = self.output_dir / f"{tag}.png"

        try:
            import win32ui  # noqa: F401
            import dde  # type: ignore
        except Exception as exc:
            raise RuntimeError("movie dde capture requires pywin32 DDE support") from exc

        last_runtime_error: Optional[RuntimeError] = None
        attempt_count = 6
        retry_delay = max(self.script_control_settle_sec, 0.2)
        for attempt in range(attempt_count):
            attempt_no = attempt + 1
            attempt_started = time.perf_counter()
            attempt_runtime_error: Optional[RuntimeError] = None

            invocation_id = uuid.uuid4().hex
            script_path = self.output_dir / f"{tag}_movie_capture_dde.{invocation_id}.tcl"
            result_path = self.output_dir / f"{tag}_movie_capture_dde.{invocation_id}.txt"
            script_text = render_dde_execute_script(
                result_path,
                "IPG-MOVIE",
                [
                    "# --- diagnostic: log View() before any set that might fail with multi-word return ---",
                    "puts $__copilot_remote_out \"DIAG_PRE: View(ev.view)='$View(ev.view)'\"",
                    "scan $View(ev.view) %d vno",
                    "scan $vno %d vno_int",
                    "set wpath \".view$vno_int\"",
                    "set vp_w [$wpath.gl0 cget -width]",
                    "set vp_h [$wpath.gl0 cget -height]",
                    "# --- diagnostic: log raw Tcl values for debugging ---",
                    "puts $__copilot_remote_out \"DIAG: vno='$vno' wpath='$wpath' vp_w='$vp_w' vp_h='$vp_h'\"",
                    "# --- diagnostic: log wm state before branching ---",
                    "set _top [winfo toplevel $wpath]",
                    "puts $__copilot_remote_out \"DIAG_WM_STATE: [wm state $_top]\"",
                    "# --- Temporarily replace CheckViewPort with no-op during height bump ---",
                    "try {",
                    "    catch {rename CheckViewPort __orig_during_bump}",
                    "    proc CheckViewPort {wv} {}",
                    "    View::SetSize $vp_w [expr {$vp_h + 1}] $wpath",
                    "    View::SetSize $vp_w $vp_h $wpath",
                    "} finally {",
                    "    catch {rename CheckViewPort {}}",
                    "    catch {rename __orig_during_bump CheckViewPort}",
                    "}",
                    "# --- Cancel UpdateView_TimerProc: rename to no-op + cancel pending timers ---",
                    "# after cancel script cancels ONLY ONE timer (Tcl 9.0.3 tclTimer.c:TimerCancelDo",
                    "# breaks after first match). update also triggers IPG-MOVIE to register NEW timers.",
                    "# rename + no-op is the only robust defense against any remaining or re-registered",
                    "# timers that call ConfigFBO -> FBO new during event processing.",
                    "try {",
                    "    catch {after cancel UpdateView_TimerProc}",
                    "    catch {rename UpdateView_TimerProc __saved_UpdateView_TimerProc}",
                    "    proc UpdateView_TimerProc {args} {}",
                    "    # --- Process pending resize events to stabilize GL context after height bump ---",
                    "    update",
                    "} finally {",
                    "    catch {rename UpdateView_TimerProc {}}",
                    "    catch {rename __saved_UpdateView_TimerProc UpdateView_TimerProc}",
                    "}",
                    "if {[wm state $_top] eq {iconic}} {",
                    "    # --- window minimized: use persistent FBO (offscreen) ---",
                    "    if {![info exists __captureFBO]} {",
                    "        set __captureFBO [FBO new $vp_w $vp_h -tex rgb -noclear]",
                    "        set __captureFBO_w $vp_w",
                    "        set __captureFBO_h $vp_h",
                    "    } elseif {$__captureFBO_w != $vp_w || $__captureFBO_h != $vp_h} {",
                    "        catch {FBO delete $__captureFBO}",
                    "        set __captureFBO [FBO new $vp_w $vp_h -tex rgb -noclear]",
                    "        set __captureFBO_w $vp_w",
                    "        set __captureFBO_h $vp_h",
                    "    }",
                    "    set update_rc [catch {",
                    "        FBO begin $__captureFBO",
                    "        UpdateView $vno_int",
                    "        FBO end",
                    "    } update_msg]",
                    "    catch {FBO end}",
                    "    puts $__copilot_remote_out \"DIAG_BRANCH: iconic\"",
                    "    if {$update_rc != 0} {",
                    "        puts $__copilot_remote_out \"DIAG_ERROR: $update_msg\"",
                    "        catch {gl bindframebuffer_read 0}",
                    "        catch {FBO delete $__captureFBO}",
                    "        catch {unset __captureFBO}",
                    "        error $update_msg",
                    "    }",
                    "    after 100",
                    "    catch {image delete probeImg}",
                    "    image create photo probeImg -width $vp_w -height $vp_h",
                    "    set read_rc [catch {",
                    "        gl bindframebuffer_read $__captureFBO",
                    "        gl readpixels 0 0 probeImg",
                    "    } read_msg]",
                    "    if {$read_rc != 0} {",
                    "        puts $__copilot_remote_out \"DIAG_ERROR: $read_msg\"",
                    "        catch {FBO delete $__captureFBO}",
                    "        catch {unset __captureFBO}",
                    "        error $read_msg",
                    "    }",
                    f'probeImg write "{out_path.as_posix()}" -format png',
                    "} else {",
                    "    puts $__copilot_remote_out \"DIAG_BRANCH: normal\"",
                    "    # --- window visible: UpdateView only; capture via win32 PrintWindow ---",
                    "    UpdateView $vno_int",
                    "    after 200",
                    "}",
                    "catch {gl bindframebuffer_read 0}",
            ],
            )
            script_path.write_text(script_text, encoding="utf-8")
            _unlink_if_exists(result_path)
            self._ensure_dde_dispatch_ready("movie_capture")

            server = None
            try:
                server = dde.CreateServer()
                server.Create(f"CopilotMovieCapture.{uuid.uuid4().hex}")
                conv = dde.CreateConversation(server)
                conv.ConnectTo(self.script_control_dde_service, self.script_control_dde_topic)
                conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
            except Exception as exc:
                attempt_runtime_error = RuntimeError(f"movie dde RunScript failed: {exc}")
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
                                attempt_runtime_error = RuntimeError(f"movie dde capture failed: {msg}")
                                break
                            self._log_dde_retry_event(
                                "movie_capture",
                                attempt_no,
                                attempt_count,
                                "success",
                                time.perf_counter() - attempt_started,
                                detail=f"output={out_path.name}",
                            )
                            self._record_dde_operation_success()
                            _unlink_if_exists(script_path)
                            _unlink_if_exists(result_path)
                            if not out_path.exists():
                                try:
                                    from capture_viewport_win32 import capture_ipgmovie_viewport
                                    capture_ipgmovie_viewport(out_path)
                                except Exception as exc:
                                    attempt_runtime_error = RuntimeError(
                                        f"movie dde capture failed: Win32 viewport capture error: {exc}",
                                    )
                                    break
                            return out_path
                    time.sleep(0.05)

            if attempt_runtime_error is None:
                attempt_runtime_error = RuntimeError("Timed out waiting for movie dde capture result")
            last_runtime_error = attempt_runtime_error
            retry_sleep_sec = retry_delay * attempt_no if attempt < attempt_count - 1 else None
            self._log_dde_retry_event(
                "movie_capture",
                attempt_no,
                attempt_count,
                "retry" if retry_sleep_sec is not None else "failed",
                time.perf_counter() - attempt_started,
                detail=attempt_runtime_error,
                retry_sleep_sec=retry_sleep_sec,
            )
            # Keep files for debugging when capture fails
            # _unlink_if_exists(script_path)
            # _unlink_if_exists(result_path)
            if retry_sleep_sec is not None:
                if self._runtime_error_needs_dde_recovery_probe(attempt_runtime_error):
                    if self._wait_for_dde_service_recovery():
                        continue
                time.sleep(retry_sleep_sec)

        if last_runtime_error is not None:
            self._record_dde_operation_failure(last_runtime_error, "movie_capture")
            raise last_runtime_error
        final_error = RuntimeError("Timed out waiting for movie dde capture result")
        self._record_dde_operation_failure(final_error, "movie_capture")
        raise final_error

    def capture_movie(self, tag: str) -> Path:
        if not getattr(self, "_movie_view_size_initialized", False):
            try:
                import cmapi_testrun_control as cmctrl
                from runtime_config_bootstrap import load_movie_view_size_from_real_image
                if self.config_path is not None and self.config_path.exists():
                    width, height = load_movie_view_size_from_real_image(self.config_path)
                    cmctrl.ensure_movie_view_size(width, height)
                    print(f"Set movie view size to {width}x{height} before first capture")
                else:
                    print(f"Skipped movie view size init: config_path={'None' if self.config_path is None else 'not found'}")
            except Exception as exc:
                print(f"Warning: could not set movie view size: {exc}")
            self._movie_view_size_initialized = True
        # Rendering health check: detect freeze before attempting capture.
        # Trigger restart for UVA=1 (stuck) or SUV=1 (stopped) or EXP=1 (exporting).
        # UVA=0+SUV=0 is the normal between-frame state — let stale hash detection
        # (Layer 2 in evaluate()) handle that case if rendering is actually dead.
        try:
            from rendering_health import check_render_state, try_restart_rendering
            state = check_render_state()
            if state.get("ok"):
                uva = state.get("uva", "0")
                suv = state.get("suv", "0")
                exp = state.get("exp", "0")
                if uva == "1" or suv == "1" or exp == "1":
                    print(f"[health] Rendering issue before '{tag}': UVA={uva} SUV={suv} EXP={exp}, attempting restart...")
                    r = try_restart_rendering()
                    if r.get("restart_success"):
                        print(f"[health] Rendering restarted, UC growth={r.get('uc_growth')}")
                    else:
                        print(f"[health] Rendering restart failed: {r.get('error', 'unknown')}")
        except ImportError:
            pass  # rendering_health module not available
        except Exception as exc:
            print(f"[health] Rendering check error: {exc}")

    def _snapshot_values(self) -> Dict[str, float]:
        return {p.name: p.value for p in self.params}

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

    def _apply_value_map(self, values: Dict[str, float]) -> None:
        touched = False
        touched_params: List[ParameterSpec] = []
        for param in self.params:
            if param.name not in values:
                continue
            param.value = float(values[param.name])
            touched_params.append(param)
            touched = True
        if touched_params:
            # Only apply params that differ from IPG-MOVIE's current values.
            # Re-applying unchanged params via widget entries + .camera.btn.set invoke
            # can trigger an internal camera model re-initialization, causing a
            # rendering shift even when nominal values are identical.
            try:
                current = self._read_script_control_values(touched_params)
                changed_params = []
                for param in touched_params:
                    expected = self._quantize_param_value(param, param.value)
                    actual = current.get(param.name)
                    if actual is None:
                        changed_params.append(param)
                        print(f"  param {param.name}: not readable from IPG-MOVIE, will apply")
                        continue
                    read_decimals = self.SCRIPT_CONTROL_READ_DECIMALS.get(param.name, param.decimals)
                    if not self._script_control_readback_matches(expected, actual, param.decimals, read_decimals):
                        changed_params.append(param)
                        print(f"  param {param.name}: differs (expected={expected}, actual={actual}), will apply")
                    else:
                        print(f"  param {param.name}: matches ({actual}), skip")
                if changed_params:
                    print(f"Applying {len(changed_params)} changed params: {[p.name for p in changed_params]}")
                    self._apply_script_control_params(changed_params)
                else:
                    print("All parameters already match IPG-MOVIE state, skipping apply")
            except Exception as exc:
                print(f"Warning: diff-only apply failed ({exc}), falling back to full apply")
                self._apply_script_control_params(touched_params)
        if touched:
            time.sleep(self.settle_sec)

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

    def _extract_roi(
        self,
        image: np.ndarray,
        roi: Optional[Tuple[int, int, int, int]],
        padding: int = 0,
    ) -> Tuple[np.ndarray, Tuple[int, int]]:
        if roi is None:
            return image, (0, 0)

        x, y, width, height = roi
        if padding > 0:
            x -= padding
            y -= padding
            width += padding * 2
            height += padding * 2
        img_h, img_w = image.shape[:2]
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(img_w, x + width)
        y1 = min(img_h, y + height)
        if x0 >= x1 or y0 >= y1:
            raise ValueError("roi is outside image bounds")
        return image[y0:y1, x0:x1], (x0, y0)

    def _detect_roi_padding_attempts(self, board: BoardProfile) -> List[int]:
        attempts = [0]
        configured_padding = max(0, int(board.detect_roi_padding))
        if configured_padding > 0:
            attempts.append(configured_padding)

        if board.roi is not None and configured_padding <= 0:
            _, _, width, height = board.roi
            base_span = max(1, int(max(width, height)))
            image_h, image_w = self.real_img.shape[:2]
            max_auto_padding = int(round(max(image_h, image_w) * 0.65))
            auto_paddings: List[int] = []

            if board.board_type == "checkerboard" or _is_aruco_family_board_type(board.board_type) or _is_apriltag_board_type(board.board_type):
                auto_paddings.extend(
                    [
                        max(120, int(round(base_span * 1.5))),
                        max(220, int(round(base_span * 2.5))),
                    ]
                )
            elif _is_custom_marker_board_type(board.board_type):
                source_roi = board.template_source_roi or board.roi
                _, _, source_w, source_h = source_roi
                source_span = max(1, int(max(source_w, source_h)))
                auto_paddings.extend(
                    [
                        max(240, int(round(source_span * 1.5))),
                        max(480, int(round(source_span * 3.0))),
                    ]
                )

            for padding_value in auto_paddings:
                attempts.append(min(max_auto_padding, padding_value))

        ordered_attempts: List[int] = []
        seen: set[int] = set()
        for padding in attempts:
            normalized = max(0, int(padding))
            if normalized in seen:
                continue
            ordered_attempts.append(normalized)
            seen.add(normalized)
        return ordered_attempts

    def _template_match_threshold_attempts(self, board: BoardProfile) -> List[float]:
        thresholds = [float(board.template_match_threshold)]
        if _is_custom_marker_board_type(board.board_type):
            thresholds.extend(
                [
                    max(0.45, float(board.template_match_threshold) * 0.85),
                    max(0.35, float(board.template_match_threshold) * 0.70),
                    0.25,
                ]
            )
        elif board.board_type == "checkerboard" and board.template_image:
            thresholds.extend(
                [
                    max(0.40, float(board.template_match_threshold) * 0.80),
                    max(0.30, float(board.template_match_threshold) * 0.65),
                ]
            )

        ordered_thresholds: List[float] = []
        seen: set[float] = set()
        for threshold in thresholds:
            normalized = round(max(0.05, min(1.0, float(threshold))), 6)
            if normalized in seen:
                continue
            ordered_thresholds.append(normalized)
            seen.add(normalized)
        return ordered_thresholds

    @staticmethod
    def _template_match_expected_bbox(board: BoardProfile) -> Optional[Tuple[int, int, int, int]]:
        if board.board_type == "custom_maker" and board.template_source_roi is not None:
            return board.template_source_roi
        return board.roi

    def _template_match_candidate_geometry(
        self,
        board: BoardProfile,
        template_shape: Tuple[int, int],
        match_crop: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[float, float, float, float]:
        template_h, template_w = template_shape[:2]
        anchor_shift_x = 0.0
        anchor_shift_y = 0.0
        candidate_w = float(template_w)
        candidate_h = float(template_h)
        if board.board_type == "custom_maker":
            content_x, content_y, candidate_w, candidate_h = self._custom_board_content_geometry(board)
            effective_match_crop = match_crop
            if effective_match_crop is None:
                template_info = self.custom_templates.get(board.board_id, {})
                effective_match_crop = template_info.get("match_crop")
            if effective_match_crop is not None:
                crop_x, crop_y, _, _ = effective_match_crop
                anchor_shift_x = content_x - float(crop_x)
                anchor_shift_y = content_y - float(crop_y)
            else:
                anchor_shift_x = content_x
                anchor_shift_y = content_y
        return anchor_shift_x, anchor_shift_y, candidate_w, candidate_h

    def _template_match_best_local_candidate(
        self,
        board: BoardProfile,
        response: np.ndarray,
        offset: Tuple[int, int],
        template_shape: Tuple[int, int],
        image_shape: Tuple[int, int],
        match_crop: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Tuple[float, Tuple[int, int]]]:
        expected_bbox = self._template_match_expected_bbox(board)
        if expected_bbox is None:
            _, max_value, _, max_location = cv2.minMaxLoc(response)
            return float(max_value), (int(max_location[0]), int(max_location[1]))

        expand_ratio = 0.12 if board.board_type == "checkerboard" else 0.06
        expand_min_pad = 20 if board.board_type == "checkerboard" else 12
        allowed_bbox = self._expand_bbox(
            expected_bbox,
            image_shape[:2],
            ratio=expand_ratio,
            min_pad=expand_min_pad,
        )
        allow_x, allow_y, allow_w, allow_h = allowed_bbox
        shift_x, shift_y, cand_w, cand_h = self._template_match_candidate_geometry(
            board,
            template_shape,
            match_crop,
        )

        response_h, response_w = response.shape[:2]
        x_coords = offset[0] + np.arange(response_w, dtype=np.float32)
        y_coords = offset[1] + np.arange(response_h, dtype=np.float32)
        grid_x, grid_y = np.meshgrid(x_coords, y_coords)
        center_x = grid_x + shift_x + (cand_w * 0.5)
        center_y = grid_y + shift_y + (cand_h * 0.5)
        valid_mask = (
            (center_x >= allow_x)
            & (center_x <= (allow_x + allow_w))
            & (center_y >= allow_y)
            & (center_y <= (allow_y + allow_h))
        )
        if not bool(np.any(valid_mask)):
            return None

        masked_response = np.where(valid_mask, response, -np.inf)
        flat_index = int(np.argmax(masked_response))
        max_value = float(masked_response.flat[flat_index])
        if not np.isfinite(max_value):
            return None
        max_y, max_x = np.unravel_index(flat_index, masked_response.shape)

        expected_center_x = allow_x + (allow_w * 0.5)
        expected_center_y = allow_y + (allow_h * 0.5)
        score_band_tolerance = 0.03 if _is_custom_marker_board_type(board.board_type) else 0.01
        near_best_mask = valid_mask & (response >= (max_value - score_band_tolerance))
        if bool(np.any(near_best_mask)):
            center_distance = np.square(center_x - expected_center_x) + np.square(
                center_y - expected_center_y
            )
            center_distance = np.where(near_best_mask, center_distance, np.inf)
            nearest_index = int(np.argmin(center_distance))
            nearest_y, nearest_x = np.unravel_index(nearest_index, center_distance.shape)
            nearest_value = float(response[nearest_y, nearest_x])
            if np.isfinite(nearest_value):
                return nearest_value, (int(nearest_x), int(nearest_y))

        return max_value, (int(max_x), int(max_y))

    def _custom_board_image_penalty(
        self,
        board: BoardProfile,
        real_detection: DetectionResult,
        sim_detection: DetectionResult,
        sim_eval_image: Optional[np.ndarray],
    ) -> float:
        if sim_eval_image is None:
            return 0.0

        real_bbox = self._points_bbox(real_detection.ordered_points)
        sim_bbox = self._points_bbox(sim_detection.ordered_points)
        rx, ry, rw, rh = real_bbox
        sx, sy, sw, sh = sim_bbox
        real_patch = self.real_img[ry : ry + rh, rx : rx + rw]
        sim_patch = sim_eval_image[sy : sy + sh, sx : sx + sw]
        if real_patch.size == 0 or sim_patch.size == 0:
            return 0.0

        target_w = max(8, min(real_patch.shape[1], sim_patch.shape[1]))
        target_h = max(8, min(real_patch.shape[0], sim_patch.shape[0]))
        real_patch = cv2.resize(real_patch, (target_w, target_h), interpolation=cv2.INTER_AREA)
        sim_patch = cv2.resize(sim_patch, (target_w, target_h), interpolation=cv2.INTER_AREA)
        real_processed = self._preprocess_template_match_image(real_patch, board)
        sim_processed = self._preprocess_template_match_image(sim_patch, board)
        mae = float(
            np.mean(np.abs(real_processed.astype(np.float32) - sim_processed.astype(np.float32)))
        ) / 255.0
        return mae * 100.0

    def _prepare_eval_image(self, image: np.ndarray) -> np.ndarray:
        source_h, source_w = image.shape[:2]
        target_h, target_w = self.real_img.shape[:2]
        if source_h <= 0 or source_w <= 0:
            raise ValueError("image has invalid shape")
        if source_w * target_h != target_w * source_h:
            print(
                "WARNING: Captured image aspect ratio does not match real_image: "
                f"captured={source_w}x{source_h}, real={target_w}x{target_h} - will resize"
            )
        if image.shape != self.real_img.shape:
            target_w = int(self.real_img.shape[1])
            target_h = int(self.real_img.shape[0])
            if self.keep_aspect_resize:
                h, w = image.shape[:2]
                scale = min(target_w / max(1, w), target_h / max(1, h))
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                resized = cv2.resize(image, (new_w, new_h))
                if resized.ndim == 2:
                    canvas = np.zeros((target_h, target_w), dtype=resized.dtype)
                else:
                    canvas = np.zeros(
                        (target_h, target_w, resized.shape[2]), dtype=resized.dtype
                    )
                off_x = (target_w - new_w) // 2
                off_y = (target_h - new_h) // 2
                canvas[off_y : off_y + new_h, off_x : off_x + new_w] = resized
                image = canvas
            else:
                image = cv2.resize(image, (target_w, target_h))
        return image

    def _get_eval_transform(self, image_shape: Tuple[int, int]) -> EvalImageTransform:
        source_h, source_w = image_shape[:2]
        target_h = int(self.real_img.shape[0])
        target_w = int(self.real_img.shape[1])

        if source_h == target_h and source_w == target_w:
            return EvalImageTransform(
                scale_x=1.0,
                scale_y=1.0,
                offset_x=0,
                offset_y=0,
                eval_width=target_w,
                eval_height=target_h,
                source_width=source_w,
                source_height=source_h,
            )

        if self.keep_aspect_resize:
            scale = min(target_w / max(1, source_w), target_h / max(1, source_h))
            eval_w = max(1, int(round(source_w * scale)))
            eval_h = max(1, int(round(source_h * scale)))
            return EvalImageTransform(
                scale_x=scale,
                scale_y=scale,
                offset_x=(target_w - eval_w) // 2,
                offset_y=(target_h - eval_h) // 2,
                eval_width=eval_w,
                eval_height=eval_h,
                source_width=source_w,
                source_height=source_h,
            )

        return EvalImageTransform(
            scale_x=target_w / max(1, source_w),
            scale_y=target_h / max(1, source_h),
            offset_x=0,
            offset_y=0,
            eval_width=target_w,
            eval_height=target_h,
            source_width=source_w,
            source_height=source_h,
        )

    @staticmethod
    def _get_annotation_palette() -> List[Tuple[int, int, int]]:
        return [
            (70, 80, 230),
            (60, 170, 90),
            (220, 110, 60),
            (180, 60, 180),
            (70, 170, 200),
            (200, 200, 70),
            (80, 220, 120),
            (255, 180, 80),
            (80, 180, 255),
            (220, 120, 255),
            (255, 120, 120),
            (120, 255, 255),
        ]

    def _map_eval_points_to_source(
        self, points: np.ndarray, transform: EvalImageTransform
    ) -> np.ndarray:
        reshaped = points.reshape(-1, 2).astype(np.float32)
        mapped = np.empty_like(reshaped)
        mapped[:, 0] = (reshaped[:, 0] - float(transform.offset_x)) / float(transform.scale_x)
        mapped[:, 1] = (reshaped[:, 1] - float(transform.offset_y)) / float(transform.scale_y)
        mapped[:, 0] = np.clip(mapped[:, 0], 0, max(0, transform.source_width - 1))
        mapped[:, 1] = np.clip(mapped[:, 1], 0, max(0, transform.source_height - 1))
        return mapped.reshape(points.shape)

    @staticmethod
    def _draw_annotated_label(
        image: np.ndarray,
        text: str,
        anchor: Tuple[int, int],
        color: Tuple[int, int, int],
        scale: float = 0.62,
        thickness: int = 2,
    ) -> None:
        font = cv2.FONT_HERSHEY_SIMPLEX
        (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
        x = max(8, int(anchor[0]))
        y = max(text_h + 12, int(anchor[1]))
        top_left = (x - 4, y - text_h - 8)
        bottom_right = (x + text_w + 6, y + baseline - 2)
        cv2.rectangle(image, top_left, bottom_right, color, -1)
        cv2.putText(
            image,
            text,
            (x, y - 4),
            font,
            scale,
            (16, 16, 16),
            thickness,
            cv2.LINE_AA,
        )

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

    @classmethod
    def _resolve_annotated_label_anchor(
        cls,
        image_shape: Tuple[int, ...],
        text: str,
        preferred_anchor: Tuple[int, int],
        occupied_boxes: List[Tuple[Tuple[int, int], Tuple[int, int]]],
        scale: float = 0.62,
        thickness: int = 2,
    ) -> Tuple[Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]:
        offsets = [0, 24, -24, 48, -48, 72, -72, 96, -96]
        for delta_y in offsets:
            top_left, bottom_right, resolved_anchor = cls._annotated_label_bounds(
                image_shape,
                text,
                (preferred_anchor[0], preferred_anchor[1] + delta_y),
                scale=scale,
                thickness=thickness,
            )
            overlaps = any(
                not (
                    bottom_right[0] < other_top_left[0]
                    or other_bottom_right[0] < top_left[0]
                    or bottom_right[1] < other_top_left[1]
                    or other_bottom_right[1] < top_left[1]
                )
                for other_top_left, other_bottom_right in occupied_boxes
            )
            if not overlaps:
                return resolved_anchor, (top_left, bottom_right)

        top_left, bottom_right, resolved_anchor = cls._annotated_label_bounds(
            image_shape,
            text,
            preferred_anchor,
            scale=scale,
            thickness=thickness,
        )
        return resolved_anchor, (top_left, bottom_right)

    def annotate_existing_image(
        self,
        image_path: Path,
        output_path: Optional[Path] = None,
        total_detail: Optional[TotalScoreDetail] = None,
        values: Optional[Dict[str, float]] = None,
    ) -> Tuple[Path, List[BoardScoreDetail]]:
        if self.real_detections is None:
            self.real_detections = self._detect_reference_boards()

        sim_gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        sim_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if sim_gray is None or sim_bgr is None:
            raise RuntimeError(f"Failed reading screenshot: {image_path}")

        transform = self._get_eval_transform(sim_gray.shape[:2])
        sim_prepared = self._prepare_eval_image(sim_gray)
        sim_score_img = self._build_sim_eval_image(sim_gray)
        palette = self._get_annotation_palette()
        board_scores: List[BoardScoreDetail] = []
        occupied_label_boxes: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []

        for index, board in enumerate(self.boards):
            color = palette[index % len(palette)]
            detection_img = sim_prepared if _is_custom_marker_board_type(board.board_type) else sim_score_img
            sim_detection = self._detect_board(detection_img, board)
            real_detection = self.real_detections[board.board_id]
            score = self._score_board(board, real_detection, sim_detection, sim_prepared)
            board_scores.append(score)

            if sim_detection.success and sim_detection.ordered_points.size > 0:
                mapped_points = self._map_eval_points_to_source(
                    sim_detection.ordered_points, transform
                ).reshape(-1, 2)
                mapped_points_int = np.round(mapped_points).astype(np.int32)
                checkerboard_outline = None
                if board.board_type == "checkerboard":
                    checkerboard_outline = self._checkerboard_outline(
                        mapped_points,
                        board.board_size,
                    )
                if checkerboard_outline is not None:
                    outline_int = np.round(checkerboard_outline).astype(np.int32).reshape(-1, 1, 2)
                    cv2.polylines(sim_bgr, [outline_int], True, color, 2, cv2.LINE_AA)
                    x, y, width, height = cv2.boundingRect(outline_int)
                elif mapped_points_int.shape[0] >= 3:
                    hull = cv2.convexHull(mapped_points_int.reshape(-1, 1, 2))
                    cv2.polylines(sim_bgr, [hull], True, color, 2, cv2.LINE_AA)
                    x, y, width, height = cv2.boundingRect(hull)
                else:
                    x, y, width, height = self._points_bbox(mapped_points)
                    cv2.rectangle(sim_bgr, (x, y), (x + width, y + height), color, 2)
                for point in mapped_points:
                    cv2.circle(
                        sim_bgr,
                        (int(round(float(point[0]))), int(round(float(point[1])))),
                        3,
                        color,
                        -1,
                    )
                label_anchor, label_box = self._resolve_annotated_label_anchor(
                    sim_bgr.shape,
                    board.board_id,
                    (x + 2, max(18, y - 10)),
                    occupied_label_boxes,
                )
                self._draw_annotated_label(
                    sim_bgr,
                    board.board_id,
                    label_anchor,
                    color,
                )
                occupied_label_boxes.append(label_box)

        total_detail = total_detail or self._aggregate_scores(board_scores, baseline_metrics=None)

        header = f"{Path(self.cfg['real_image']).name} compare on {image_path.name}"
        summary = (
            f"total={total_detail.total_score:.6f} "
            f"compared={total_detail.compared_board_count} "
            f"degrade={total_detail.degrade_penalty:.3f}"
        )
        isolated_outlier_text = None
        if total_detail.isolated_outlier_boards:
            isolated_outlier_text = "excluded=" + ", ".join(total_detail.isolated_outlier_boards)

        param_values = values or self._snapshot_values()
        info_lines: List[Tuple[str, Tuple[int, int, int]]] = [
            (header, (245, 245, 245)),
            (summary, (230, 235, 245)),
        ]
        if isolated_outlier_text:
            info_lines.append((isolated_outlier_text, (180, 180, 240)))
        info_lines.extend(
            (line, (220, 245, 220)) for line in self._format_value_lines(param_values)
        )
        legend_lines = _build_annotation_legend_lines(total_detail)
        for index, legend_text in enumerate(legend_lines):
            color = palette[index % len(palette)]
            info_lines.append((legend_text, color))

        font = cv2.FONT_HERSHEY_SIMPLEX
        panel_scale = 0.58
        panel_thickness = 2
        panel_gap = 24
        panel_left = 16
        panel_top = 24
        line_step = 30
        panel_min_width = 340
        max_text_width = 0
        for text, _ in info_lines:
            (text_w, _), _ = cv2.getTextSize(text, font, panel_scale, panel_thickness)
            max_text_width = max(max_text_width, text_w)
        panel_width = max(panel_min_width, max_text_width + 36)

        canvas_height, canvas_width = sim_bgr.shape[:2]
        annotated_canvas = np.full(
            (canvas_height, canvas_width + panel_gap + panel_width, 3),
            236,
            dtype=np.uint8,
        )
        annotated_canvas[:, :canvas_width] = sim_bgr
        separator_x = canvas_width + (panel_gap // 2)
        cv2.line(
            annotated_canvas,
            (separator_x, 0),
            (separator_x, canvas_height - 1),
            (205, 205, 205),
            2,
        )

        panel_x = canvas_width + panel_gap + panel_left
        panel_y = panel_top
        for text, color in info_lines:
            self._draw_annotated_label(
                annotated_canvas,
                text,
                (panel_x, panel_y),
                color,
                scale=panel_scale,
                thickness=panel_thickness,
            )
            panel_y += line_step

        final_output = output_path or image_path.with_name(f"{image_path.stem}_annotated.png")
        final_output.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(final_output), annotated_canvas)
        return final_output, total_detail.board_scores

    @staticmethod
    def _best_score_image_output_path(image_path: Path) -> Path:
        return image_path.with_name(f"{image_path.stem}_score.png")

    @staticmethod
    def _best_overlay_image_output_path(image_path: Path) -> Path:
        return image_path.with_name(f"{image_path.stem}_overlay.png")

    def _ensure_best_score_image(
        self,
        image_path: Path,
        total_detail: TotalScoreDetail,
        values: Optional[Dict[str, float]] = None,
    ) -> Optional[Path]:
        if not self.auto_generate_best_score_image:
            return None

        cache_key = str(image_path.resolve())
        output_path = self._best_score_image_output_path(image_path)
        cached = self._best_score_image_cache.get(cache_key)
        if cached is not None and cached.exists():
            return cached
        if output_path.exists():
            try:
                if output_path.stat().st_mtime >= image_path.stat().st_mtime:
                    self._best_score_image_cache[cache_key] = output_path
                    return output_path
            except OSError:
                pass

        annotated_path, _ = self.annotate_existing_image(
            image_path,
            output_path=output_path,
            total_detail=total_detail,
            values=values,
        )
        self._best_score_image_cache[cache_key] = annotated_path
        return annotated_path

    def _build_score_image_for_snapshot(
        self,
        image_path: Path,
        total_detail: TotalScoreDetail,
        values: Dict[str, float],
        output_path: Optional[Path] = None,
    ) -> Optional[Path]:
        if not self.auto_generate_best_score_image:
            return None

        final_output = output_path or self._best_score_image_output_path(image_path)
        annotated_path, _ = self.annotate_existing_image(
            image_path,
            output_path=final_output,
            total_detail=total_detail,
            values=values,
        )
        return annotated_path

    def _ensure_best_overlay_image(self, image_path: Path) -> Optional[Path]:
        if not self.auto_generate_best_overlay_image:
            return None

        cache_key = str(image_path.resolve())
        output_path = self._best_overlay_image_output_path(image_path)
        cached = self._best_overlay_image_cache.get(cache_key)
        if cached is not None and cached.exists():
            return cached
        if output_path.exists():
            try:
                if output_path.stat().st_mtime >= image_path.stat().st_mtime:
                    self._best_overlay_image_cache[cache_key] = output_path
                    return output_path
            except OSError:
                pass

        sim_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if sim_bgr is None:
            raise RuntimeError(f"Failed reading best image for overlay: {image_path}")
        sim_eval = self._prepare_eval_image(sim_bgr)
        real_eval = self._prepare_eval_image(self.real_img_color.copy())
        overlay = cv2.addWeighted(
            sim_eval,
            1.0 - self.overlay_visual_real_alpha,
            real_eval,
            self.overlay_visual_real_alpha,
            0.0,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), overlay)
        self._best_overlay_image_cache[cache_key] = output_path
        return output_path

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

    def _build_sim_eval_image(self, captured_gray: np.ndarray) -> np.ndarray:
        eval_image = self._prepare_eval_image(captured_gray)
        if self.comparison_mode == "direct":
            return eval_image

        residual = cv2.absdiff(eval_image, self.real_img)
        if self.overlay_residual_blur and self.overlay_residual_blur > 1:
            blur_size = int(self.overlay_residual_blur)
            if blur_size % 2 == 0:
                blur_size += 1
            residual = cv2.GaussianBlur(residual, (blur_size, blur_size), 0)

        if self.overlay_residual_threshold > 0:
            _, residual = cv2.threshold(
                residual,
                float(self.overlay_residual_threshold),
                255,
                cv2.THRESH_TOZERO,
            )

        if int(np.max(residual)) > 0:
            residual = cv2.normalize(residual, None, 0, 255, cv2.NORM_MINMAX)
        return residual.astype(np.uint8)

    @staticmethod
    def _preprocess_variants(gray: np.ndarray) -> List[np.ndarray]:
        variants = [gray]
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        variants.append(clahe.apply(gray))
        variants.append(cv2.GaussianBlur(gray, (3, 3), 0))
        return variants

    @staticmethod
    def _resolve_aruco_dictionary(dictionary_name: str):
        if not hasattr(cv2, "aruco"):
            raise RuntimeError("OpenCV aruco module is unavailable")

        normalized_name = str(dictionary_name or "DICT_4X4_50").strip().upper()
        if normalized_name.startswith("CV2.ARUCO."):
            normalized_name = normalized_name.split(".")[-1]
        dictionary_id = getattr(cv2.aruco, normalized_name, None)
        if dictionary_id is None:
            raise ValueError(f"Unsupported ArUco dictionary: {dictionary_name}")
        return cv2.aruco.getPredefinedDictionary(dictionary_id)

    @staticmethod
    def _flatten_aruco_marker_points(
        marker_corners: Sequence[np.ndarray],
        marker_ids: Optional[np.ndarray],
        offset: Tuple[int, int],
    ) -> np.ndarray:
        if marker_ids is None or not marker_corners:
            return np.empty((0, 2), dtype=np.float32)

        flat_ids = np.asarray(marker_ids, dtype=np.int32).reshape(-1)
        ordered_markers: List[Tuple[int, np.ndarray]] = []
        for index, marker_id in enumerate(flat_ids):
            if index >= len(marker_corners):
                break
            corners = np.asarray(marker_corners[index], dtype=np.float32).reshape(-1, 2)
            if corners.shape[0] < 4:
                continue
            ordered_markers.append((int(marker_id), corners))

        if not ordered_markers:
            return np.empty((0, 2), dtype=np.float32)

        ordered_markers.sort(key=lambda item: item[0])
        ordered_points = np.concatenate([item[1] for item in ordered_markers], axis=0)
        ordered_points[:, 0] += float(offset[0])
        ordered_points[:, 1] += float(offset[1])
        return ordered_points.astype(np.float32)

    def _detect_checkerboard(
        self, gray_image: np.ndarray, board: BoardProfile
    ) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)

        found = False
        corners = None
        offset = (0, 0)
        for padding in roi_attempts:
            roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
            for candidate in self._preprocess_variants(roi_img):
                flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
                found, corners = cv2.findChessboardCornersSB(candidate, board.board_size, flags=flags)
                if found and corners is not None:
                    break

                fallback_flags = (
                    cv2.CALIB_CB_ADAPTIVE_THRESH
                    | cv2.CALIB_CB_NORMALIZE_IMAGE
                    | cv2.CALIB_CB_FAST_CHECK
                )
                found, corners = cv2.findChessboardCorners(candidate, board.board_size, fallback_flags)
                if found and corners is not None:
                    criteria = (
                        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER,
                        30,
                        0.001,
                    )
                    corners = cv2.cornerSubPix(candidate, corners, (11, 11), (-1, -1), criteria)
                    break
            if found and corners is not None:
                break

        if not found or corners is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="checkerboard",
                error_message="checkerboard not detected",
            )

        ordered_points = corners.reshape(-1, 2).astype(np.float32)
        ordered_points[:, 0] += float(offset[0])
        ordered_points[:, 1] += float(offset[1])
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(len(ordered_points)),
            ordered_points=ordered_points,
            board_type=board.board_type,
            roi_used=board.roi,
            detector="checkerboard",
        )

    def _detect_aruco(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)
        dictionary = self._resolve_aruco_dictionary(board.aruco_dictionary)
        detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())

        for padding in roi_attempts:
            roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
            marker_corners, marker_ids, _ = detector.detectMarkers(roi_img)
            ordered_points = self._flatten_aruco_marker_points(marker_corners, marker_ids, offset)
            if ordered_points.shape[0] == 0:
                continue
            return DetectionResult(
                board_id=board.board_id,
                success=True,
                point_count=int(ordered_points.shape[0]),
                ordered_points=ordered_points,
                board_type=board.board_type,
                roi_used=board.roi,
                detector="aruco",
            )

        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="aruco",
            error_message="aruco markers not detected",
        )

    _APRILTAG_FAMILY_CANDIDATES = (
        "tagStandard41h12",
        "tag36h11",
        "tag25h9",
        "tag16h5",
    )

    @staticmethod
    def _flatten_apriltag_detections(
        detections: list,
        offset: Tuple[int, int],
    ) -> np.ndarray:
        if not detections:
            return np.empty((0, 2), dtype=np.float32)
        ordered = sorted(detections, key=lambda d: d.tag_id)
        points: List[np.ndarray] = []
        for det in ordered:
            corners = np.asarray(det.corners, dtype=np.float32).reshape(-1, 2)
            if corners.shape[0] < 4:
                continue
            points.append(corners)
        if not points:
            return np.empty((0, 2), dtype=np.float32)
        all_points = np.concatenate(points, axis=0)
        all_points[:, 0] += float(offset[0])
        all_points[:, 1] += float(offset[1])
        return all_points.astype(np.float32)

    def _create_apriltag_detector(self, tag_family: str) -> object:
        try:
            from pupil_apriltags import Detector
        except ImportError:
            raise RuntimeError(
                "pupil_apriltags is not installed. "
                "Install it with: pip install pupil_apriltags"
            )
        return Detector(families=tag_family)

    def _detect_apriltag(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)

        tag_family = str(board.tag_family or "").strip()
        auto_detect_family = not tag_family or tag_family.lower() == "auto"
        family_candidates = (
            list(self._APRILTAG_FAMILY_CANDIDATES) if auto_detect_family else [tag_family]
        )

        for family in family_candidates:
            try:
                detector = self._create_apriltag_detector(family)
            except Exception:
                if auto_detect_family:
                    continue
                raise

            for padding in roi_attempts:
                roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
                detections = detector.detect(roi_img)
                ordered_points = self._flatten_apriltag_detections(detections, offset)
                if ordered_points.shape[0] == 0:
                    continue
                return DetectionResult(
                    board_id=board.board_id,
                    success=True,
                    point_count=int(ordered_points.shape[0]),
                    ordered_points=ordered_points,
                    board_type=board.board_type,
                    roi_used=board.roi,
                    detector="apriltag",
                )

        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="apriltag",
            error_message="apriltag markers not detected",
        )

    def _detect_charuco(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        if board.board_size is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="charuco",
                error_message="missing charuco board_size",
            )

        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)
        dictionary = self._resolve_aruco_dictionary(board.aruco_dictionary)
        marker_length_ratio = min(max(float(board.marker_length_ratio), 1e-3), 0.999)
        square_length = max(float(board.square_size), 1e-6)
        marker_length = square_length * marker_length_ratio
        charuco_board = cv2.aruco.CharucoBoard(
            board.board_size,
            square_length,
            marker_length,
            dictionary,
        )
        detector = cv2.aruco.CharucoDetector(charuco_board)
        best_error_message = "charuco corners not detected"

        for padding in roi_attempts:
            roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
            charuco_corners, charuco_ids, _, marker_ids = detector.detectBoard(roi_img)
            if charuco_corners is None or charuco_ids is None:
                if marker_ids is not None and len(np.asarray(marker_ids).reshape(-1)) > 0:
                    best_error_message = "charuco markers found but corners not interpolated"
                continue

            ordered_points = np.asarray(charuco_corners, dtype=np.float32).reshape(-1, 2)
            flat_ids = np.asarray(charuco_ids, dtype=np.int32).reshape(-1)
            if ordered_points.shape[0] == 0 or flat_ids.shape[0] == 0:
                continue
            ordered_points = ordered_points[np.argsort(flat_ids)]
            ordered_points[:, 0] += float(offset[0])
            ordered_points[:, 1] += float(offset[1])
            return DetectionResult(
                board_id=board.board_id,
                success=True,
                point_count=int(ordered_points.shape[0]),
                ordered_points=ordered_points.astype(np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="charuco",
            )

        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="charuco",
            error_message=best_error_message,
        )

    def _detect_template_board(
        self,
        gray_image: np.ndarray,
        board: BoardProfile,
        search_mask: Optional[np.ndarray] = None,
    ) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_img, offset = self._extract_roi(eval_image, board.roi)
        roi_mask = None
        if search_mask is not None:
            prepared_mask = self._prepare_eval_image(search_mask)
            roi_mask, _ = self._extract_roi(prepared_mask, board.roi)
            roi_mask = roi_mask.astype(np.uint8)
            if int(cv2.countNonZero(roi_mask)) < max(8, board.min_match_count):
                return DetectionResult(
                    board_id=board.board_id,
                    success=False,
                    point_count=0,
                    ordered_points=np.empty((0, 2), dtype=np.float32),
                    board_type=board.board_type,
                    roi_used=board.roi,
                    detector="template",
                    error_message="search mask exhausted",
                )

        template_info = self.custom_templates.get(board.board_id)
        if template_info is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="missing template info",
            )

        kp_scene, des_scene = self.orb.detectAndCompute(roi_img, roi_mask)
        if des_scene is None or kp_scene is None or len(kp_scene) < board.min_match_count:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="insufficient custom board features",
            )

        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        raw_matches = bf.knnMatch(template_info["des"], des_scene, k=2)
        good_matches = []
        for pair in raw_matches:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < 0.75 * n.distance:
                good_matches.append(m)

        if len(good_matches) < max(8, board.min_match_count):
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="insufficient homography matches",
            )

        src_pts = np.float32([template_info["kp"][m.queryIdx].pt for m in good_matches]).reshape(
            -1, 1, 2
        )
        dst_pts = np.float32([kp_scene[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        homography, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 4.0)
        if homography is None or mask is None or int(mask.sum()) < 8:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="custom board homography failed",
            )

        projected = cv2.perspectiveTransform(
            template_info["anchors"].reshape(-1, 1, 2), homography
        ).reshape(-1, 2)
        projected[:, 0] += float(offset[0])
        projected[:, 1] += float(offset[1])
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(projected.shape[0]),
            ordered_points=projected.astype(np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="template",
        )

    def _detect_template_match_board(
        self, gray_image: np.ndarray, board: BoardProfile
    ) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)
        threshold_attempts = self._template_match_threshold_attempts(board)

        template_info = self.custom_templates.get(board.board_id)
        if template_info is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template_match",
                error_message="missing template info",
            )

        template_gray = template_info["template"]
        template_variants: List[Tuple[np.ndarray, Tuple[int, int, int, int]]] = []
        match_template_gray = template_info.get("match_template")
        match_crop = template_info.get("match_crop")
        if match_template_gray is not None and match_crop is not None:
            template_variants.append((match_template_gray, match_crop))
        template_variants.append(
            (template_gray, (0, 0, int(template_gray.shape[1]), int(template_gray.shape[0])))
        )
        best_failure_message = "search roi smaller than template"
        best_failure_value: Optional[float] = None
        match_x: Optional[float] = None
        match_y: Optional[float] = None
        matched_crop: Tuple[int, int, int, int] = (0, 0, int(template_gray.shape[1]), int(template_gray.shape[0]))
        matched_template_shape: Tuple[int, int] = (int(template_gray.shape[0]), int(template_gray.shape[1]))
        offset = (0, 0)

        for padding in roi_attempts:
            roi_img, current_offset = self._extract_roi(eval_image, board.roi, padding=padding)
            search_image = self._preprocess_template_match_image(roi_img, board)
            for variant_gray, variant_crop in template_variants:
                if (
                    roi_img.shape[0] < variant_gray.shape[0]
                    or roi_img.shape[1] < variant_gray.shape[1]
                ):
                    continue

                template_image = self._preprocess_template_match_image(variant_gray, board)
                response = cv2.matchTemplate(search_image, template_image, cv2.TM_CCOEFF_NORMED)
                best_candidate = self._template_match_best_local_candidate(
                    board,
                    response,
                    current_offset,
                    variant_gray.shape,
                    eval_image.shape,
                    variant_crop,
                )
                if best_candidate is None:
                    continue
                current_max_value, current_max_location = best_candidate
                for threshold in threshold_attempts:
                    if best_failure_value is None or current_max_value > best_failure_value:
                        best_failure_value = float(current_max_value)
                        best_failure_message = (
                            f"template match below threshold: {current_max_value:.3f} < "
                            f"{threshold:.3f}"
                        )
                    if current_max_value < threshold:
                        continue

                    offset = current_offset
                    match_x = float(offset[0] + current_max_location[0])
                    match_y = float(offset[1] + current_max_location[1])
                    matched_crop = variant_crop
                    matched_template_shape = (int(variant_gray.shape[0]), int(variant_gray.shape[1]))
                    break
                if match_x is not None and match_y is not None:
                    break
            if match_x is not None and match_y is not None:
                break

        if match_x is None or match_y is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template_match",
                error_message=best_failure_message,
            )

        template_h, template_w = matched_template_shape
        anchor_x = match_x
        anchor_y = match_y
        anchor_w = float(template_w)
        anchor_h = float(template_h)
        if board.board_type == "custom_maker":
            content_x, content_y, content_w, content_h = self._custom_board_content_geometry(board)
            crop_x, crop_y, _, _ = matched_crop
            anchor_x = match_x - float(crop_x) + content_x
            anchor_y = match_y - float(crop_y) + content_y
            anchor_w = float(content_w)
            anchor_h = float(content_h)
        anchors = np.array(
            [
                [anchor_x, anchor_y],
                [anchor_x + anchor_w - 1.0, anchor_y],
                [anchor_x + anchor_w - 1.0, anchor_y + anchor_h - 1.0],
                [anchor_x, anchor_y + anchor_h - 1.0],
                [anchor_x + anchor_w * 0.5, anchor_y + anchor_h * 0.5],
                [anchor_x + anchor_w * 0.25, anchor_y + anchor_h * 0.25],
                [anchor_x + anchor_w * 0.75, anchor_y + anchor_h * 0.25],
                [anchor_x + anchor_w * 0.75, anchor_y + anchor_h * 0.75],
                [anchor_x + anchor_w * 0.25, anchor_y + anchor_h * 0.75],
            ],
            dtype=np.float32,
        )
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(anchors.shape[0]),
            ordered_points=anchors,
            board_type=board.board_type,
            roi_used=board.roi,
            detector="template_match",
        )

    def _detect_custom_groundmaker(
        self, gray_image: np.ndarray, board: BoardProfile
    ) -> DetectionResult:
        if board.custom_detector == "template_match":
            return self._detect_template_match_board(gray_image, board)
        return self._detect_template_board(gray_image, board)

    def _detect_board(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        if board.board_type == "checkerboard":
            primary = self._detect_checkerboard(gray_image, board)
            if board.template_image:
                fallback_detectors = [self._detect_template_match_board]
                if primary.success and board.roi is not None:
                    primary_bbox = self._points_bbox(primary.ordered_points)
                    expected_bbox = self._expand_bbox(
                        board.roi,
                        self.real_img.shape[:2],
                        ratio=0.20,
                        min_pad=24,
                    )
                    primary_center_x, primary_center_y = self._bbox_center(primary_bbox)
                    exp_x, exp_y, exp_w, exp_h = expected_bbox
                    primary_matches_roi = (
                        self._bbox_iou(primary_bbox, expected_bbox) > 0.0
                        or (
                            exp_x <= primary_center_x <= (exp_x + exp_w)
                            and exp_y <= primary_center_y <= (exp_y + exp_h)
                        )
                    )
                    if primary_matches_roi:
                        return primary
                elif primary.success:
                    return primary
                for detector in fallback_detectors:
                    fallback = detector(gray_image, board)
                    if fallback.success:
                        return fallback
            if primary.success:
                return primary
            return primary
        if board.board_type == "aruco":
            return self._detect_aruco(gray_image, board)
        if board.board_type == "charuco":
            return self._detect_charuco(gray_image, board)
        if _is_apriltag_board_type(board.board_type):
            return self._detect_apriltag(gray_image, board)
        if _is_custom_marker_board_type(board.board_type):
            return self._detect_custom_groundmaker(gray_image, board)
        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="unknown",
            error_message=f"unsupported board_type: {board.board_type}",
        )

    def _detect_template_instances(
        self, gray_image: np.ndarray, board: BoardProfile, max_instances: int
    ) -> List[Tuple[DetectionResult, Tuple[int, int, int, int]]]:
        prepared = self._prepare_eval_image(gray_image)
        search_mask = np.full(prepared.shape[:2], 255, dtype=np.uint8)
        detections: List[Tuple[DetectionResult, Tuple[int, int, int, int]]] = []

        for _ in range(max_instances):
            detection = self._detect_template_board(prepared, board, search_mask=search_mask)
            if not detection.success or detection.ordered_points.size == 0:
                break

            bbox = self._expand_bbox(
                self._points_bbox(detection.ordered_points), prepared.shape[:2]
            )
            is_duplicate = any(self._bbox_iou(bbox, existing_bbox) >= 0.6 for _, existing_bbox in detections)
            x, y, width, height = bbox
            search_mask[y : y + height, x : x + width] = 0
            if is_duplicate:
                continue
            detections.append((detection, bbox))

        return detections

    def _detect_checkerboard_instances(
        self, gray_image: np.ndarray, board: BoardProfile, max_instances: int
    ) -> List[Tuple[DetectionResult, Tuple[int, int, int, int]]]:
        prepared = self._prepare_eval_image(gray_image)
        working = prepared.copy()
        detections: List[Tuple[DetectionResult, Tuple[int, int, int, int]]] = []

        for _ in range(max_instances):
            detection = self._detect_checkerboard(working, board)
            if not detection.success or detection.ordered_points.size == 0:
                break

            geometry_ratio, mean_step = self._checkerboard_geometry_ratio(detection, board)
            if geometry_ratio > 6.0:
                suppress_radius = max(8, int(round(mean_step * 0.9)))
                for point in detection.ordered_points.reshape(-1, 2):
                    cv2.circle(
                        working,
                        (int(round(float(point[0]))), int(round(float(point[1])))),
                        suppress_radius,
                        0,
                        -1,
                    )
                continue

            bbox = self._expand_bbox(
                self._points_bbox(detection.ordered_points), prepared.shape[:2]
            )
            is_duplicate = any(self._bbox_iou(bbox, existing_bbox) >= 0.6 for _, existing_bbox in detections)
            x, y, width, height = bbox
            working[y : y + height, x : x + width] = 0
            if is_duplicate:
                continue
            detections.append((detection, bbox))

        return detections

    @staticmethod
    def _checkerboard_geometry_ratio(
        detection: DetectionResult, board: BoardProfile
    ) -> Tuple[float, float]:
        if board.board_size is None or detection.ordered_points.size == 0:
            return float("inf"), 0.0
        cols, rows = board.board_size
        try:
            points = detection.ordered_points.reshape(rows, cols, 2)
        except ValueError:
            return float("inf"), 0.0

        dx = np.linalg.norm(points[:, 1:, :] - points[:, :-1, :], axis=2)
        dy = np.linalg.norm(points[1:, :, :] - points[:-1, :, :], axis=2)
        steps = np.concatenate([dx.ravel(), dy.ravel()])
        if steps.size == 0:
            return float("inf"), 0.0
        min_step = float(np.min(steps))
        max_step = float(np.max(steps))
        mean_step = float(np.mean(steps))
        if min_step <= 1e-6:
            return float("inf"), mean_step
        return max_step / min_step, mean_step

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

    def _detect_reference_boards(self) -> Dict[str, DetectionResult]:
        detections: Dict[str, DetectionResult] = {}
        visible_count = 0
        for board in self.boards:
            detection = self._reference_detection_from_board_geometry(board)
            if detection is None:
                detection = self._detect_board(self.real_img, board)
            if self._is_visible(detection, self._effective_detection_min_points(board, detection)):
                visible_count += 1
            detections[board.board_id] = detection
        if visible_count == 0:
            raise RuntimeError(
                "No boards are visible in reference image. "
                "Cannot optimize without comparable targets."
            )
        return detections

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
            return BoardScoreDetail(
                board_id=board.board_id,
                board_type=board.board_type,
                success=False,
                compared=True,
                reference_visible=real_visible,
                sim_visible=False,
                total_score=board.fail_penalty,
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
        deltas = sim_points - real_points
        distances = np.linalg.norm(deltas, axis=1)
        rmse = float(np.sqrt(np.mean(np.square(distances))))
        mean_error = float(np.mean(distances))
        max_error = float(np.max(distances))
        if _is_custom_marker_board_type(board.board_type):
            image_penalty = self._custom_board_image_penalty(
                board,
                real_detection,
                sim_detection,
                sim_eval_image,
            )
            rmse = max(rmse, image_penalty)
            mean_error = max(mean_error, image_penalty)
            max_error = max(max_error, image_penalty)
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

    def _clamp_strategy_step_scale(self, step_scale: float) -> float:
        return min(self.strategy_max_step_scale, max(self.strategy_min_step_scale, step_scale))

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

    def _ordered_params_for_iteration(self) -> List[ParameterSpec]:
        if not (self.strategy_adaptation_enabled and self.strategy_reorder_params):
            return list(self.params)
        return sorted(
            self.params,
            key=lambda param: (
                -float(self.strategy_param_state.get(param.name, {}).get("priority_score", 0.0)),
                self.param_order_index.get(param.name, len(self.param_order_index)),
            ),
        )

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

    def evaluate(
        self, tag: str, baseline_metrics: Optional[Dict[str, Dict[str, float]]]
    ) -> Tuple[TotalScoreDetail, Path]:
        if self.real_detections is None:
            self.real_detections = self._detect_reference_boards()

        sim_path = self.capture_movie(tag)
        self._last_eval_image = str(sim_path)
        sim_img = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
        if sim_img is None:
            raise RuntimeError(f"Failed reading screenshot: {sim_path}")

        # Freshness check: detect stale capture (same pixel data as previous)
        current_hash = hash(sim_img.tobytes())
        prev_hash = getattr(self, "_last_capture_hash", None)
        if prev_hash is not None and current_hash == prev_hash:
            stale_count = getattr(self, "_consecutive_stale_count", 0) + 1
            self._consecutive_stale_count = stale_count
            print(f"[health] Stale capture #{stale_count} for '{tag}': image identical to previous. Attempting recovery...")
            if stale_count >= 3:
                raise RuntimeError(f"RENDERING_BROKEN: Too many consecutive stale captures ({stale_count}), rendering permanently frozen")
            try:
                from rendering_health import try_restart_rendering
                r = try_restart_rendering()
                if r.get("restart_success"):
                    print(f"[health] Rendering restarted (UC growth={r.get('uc_growth')}), re-capturing...")
                    sim_path = self.capture_movie(tag + "_re")
                    sim_img = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
                    if sim_img is None:
                        raise RuntimeError(f"Failed reading re-captured screenshot: {sim_path}")
                    re_hash = hash(sim_img.tobytes())
                    if re_hash == current_hash:
                        raise RuntimeError(f"Stale capture persists after rendering restart: {sim_path}")
                    current_hash = re_hash
                else:
                    raise RuntimeError(f"Stale capture and rendering restart failed: {r.get('error', 'unknown')}")
            except RuntimeError:
                raise  # Propagate explicit RuntimeErrors above
            except ImportError:
                raise RuntimeError(f"Stale capture detected (rendering_health not available): {sim_path}")
            except Exception as exc:
                raise RuntimeError(f"Stale capture recovery failed: {exc}")
        else:
            self._consecutive_stale_count = 0
        self._last_capture_hash = current_hash

        sim_prepared = self._prepare_eval_image(sim_img)
        sim_score_img = self._build_sim_eval_image(sim_img)
        board_scores: List[BoardScoreDetail] = []
        for board in self.boards:
            real_detection = self.real_detections[board.board_id]
            detection_img = sim_prepared if _is_custom_marker_board_type(board.board_type) else sim_score_img
            sim_detection = self._detect_board(detection_img, board)
            board_scores.append(self._score_board(board, real_detection, sim_detection, sim_prepared))

        total_detail = self._aggregate_scores(board_scores, baseline_metrics)
        return total_detail, sim_path

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

    def _optimize_coordinate_descent(self) -> dict:
        return self._optimize_coordinate_descent_impl()

    def _optimize_bayesian(self) -> dict:
        if not _OPTUNA_AVAILABLE:
            print("WARNING: optuna not installed, falling back to coordinate_descent")
            return self._optimize_coordinate_descent_impl()
        return self._optimize_bayesian_impl()

    def _optimize_bayesian_impl(self) -> dict:
        self._ensure_live_log()
        self._historical_best_snapshot = None
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
            history=history,
            in_progress=True,
        )

        def objective(trial: optuna.Trial) -> float:
            trial_values = {}
            self._total_trial_count += 1
            for p in self.params:
                trial_values[p.name] = trial.suggest_float(
                    p.name,
                    float(p.min_value),
                    float(p.max_value),
                )

            self._apply_value_map_or_recover(
                trial_values,
                f"Failed to apply trial values in Bayesian iteration {trial.number}",
            )

            tag = f"bayesian_trial_{trial.number:04d}"
            try:
                total_detail, img_path = self.evaluate(
                    tag,
                    baseline_metrics=best_baseline,
                )
                score = total_detail.total_score

                accepted, accepted_reason = self._acceptance_decision(
                    baseline_score=best_score,
                    baseline_detail=best_total_detail,
                    candidate_score=score,
                    candidate_detail=total_detail,
                )

                history.append(
                    self._make_history_entry(
                        trial.number + 1,
                        total_detail,
                        img_path,
                        accepted,
                        meta={
                            "phase": "bayesian",
                            "values": trial_values.copy(),
                            "accepted_reason": accepted_reason,
                        },
                    )
                )
                history_trimmed = self._trim_history(history)

                self._append_trial_log(
                    iteration=trial.number + 1,
                    score=score,
                    accepted=accepted,
                    phase="bayesian",
                    accepted_reason=accepted_reason,
                )

                print(
                    f"iter={trial.number + 1} phase=bayesian "
                    f"total_score={score:.6f} compared={total_detail.compared_board_count} "
                    f"degrade={total_detail.degrade_penalty:.6f} "
                    f"critical_degrade={total_detail.has_critical_degrade} "
                    f"accepted={accepted} accepted_reason={accepted_reason} "
                    f"{self._top_board_summary(total_detail)}"
                )

                if trial.number % self.progress_flush_every == 0:
                    self._write_progress_result(
                        best_score=best_score,
                        best_values=best_values,
                        best_total_detail=best_total_detail,
                        best_img=best_img,
                        stop_reason="running",
                        history=history_trimmed,
                        in_progress=True,
                    )

                return score
            except RuntimeError as exc:
                print(f"iter={trial.number + 1} phase=bayesian runtime_error={exc}")
                self._apply_value_map_or_recover(
                    best_values,
                    f"Failed to restore best values after Bayesian runtime error: {exc}",
                )
                history.append(
                    self._make_history_entry(
                        trial.number + 1,
                        best_total_detail,
                        best_img,
                        False,
                        failed_reason=str(exc),
                        meta={"phase": "bayesian_runtime_error"},
                    )
                )
                self._total_trial_count += 1
                self._append_trial_log(
                    iteration=trial.number + 1,
                    score=float("inf"),
                    accepted=False,
                    phase="bayesian_runtime_error",
                    failed_reason=str(exc),
                )
                return float("inf")

        sampler = optuna.samplers.TPESampler(
            seed=int(cfg.get("bayesian_seed", 42)),
            n_startup_trials=min(self.max_iters // 2, 10),
        )
        study = optuna.create_study(
            direction="minimize",
            sampler=sampler,
            study_name=f"calibration_{self.camera_name}",
        )

        study.enqueue_trial(best_values)

        n_trials = self.max_iters
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

        if study.best_trial is not None:
            best_params = study.best_params
            best_score = study.best_value
            self._apply_value_map_or_recover(
                best_params,
                "Failed to apply best Bayesian parameters",
            )
            best_total_detail, best_img = self.evaluate(
                "bayesian_best",
                baseline_metrics=None,
            )
            best_values = best_params.copy()
            stop_reason = "bayesian_converged"

        result = self._build_result_payload(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            best_score_image=self._ensure_best_score_image(
                best_img,
                best_total_detail,
                values=best_values,
            ),
            best_overlay_image=self._ensure_best_overlay_image(best_img),
            stop_reason=stop_reason,
            history=self._trim_history(history),
            in_progress=False,
        )
        self._print_acceptance_summary(best_total_detail)
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
    ) -> "CameraCalibrator.TrialResult":
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
    ) -> "CameraCalibrator.TrialResult":
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

    def _optimize_coordinate_descent_impl(self) -> dict:
        self._ensure_live_log()
        self._historical_best_snapshot = None
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
        while it <= self.max_iters:
            improved_in_iter = False
            self._total_iteration_count += 1
            base_values = self._snapshot_values()
            base_score = best_score
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

            for p in ordered_params:
                preferred_direction = self.preferred_directions.get(p.name, 1.0)
                trial_directions: List[float] = [preferred_direction, -preferred_direction]
                best_param_move: Optional[Dict[str, object]] = None
                seen_trial_values: set[float] = set()
                stop_param_search = False
                effective_step = self._strategy_effective_step(p)

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
                            if it > self.max_iters:
                                stop_param_search = True
                                break
                            continue

                        total_detail = trial_result.total_detail
                        img_path = trial_result.img_path
                        score = trial_result.score
                        accepted = trial_result.accepted
                        accepted_reason = trial_result.accepted_reason
                        joint_candidate_reason = trial_result.joint_candidate_reason

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

                        it += 1
                        if it > self.max_iters:
                            stop_param_search = True
                            break
                        if (
                            accepted
                            and self.stop_after_first_accepted_direction
                            and not self._is_joint_exploration_param(p.name)
                        ):
                            stop_param_search = True
                            break

                    if stop_param_search:
                        break

                if best_param_move is None:
                    p.step = max(p.min_step, p.step * self.step_decay)
                else:
                    candidate_moves.append(best_param_move)

                if it > self.max_iters:
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

            if candidate_moves and it <= self.max_iters:
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
                        if it > self.max_iters:
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
                    if it > self.max_iters:
                        break

                if accepted_params_in_pass:
                    best_score = joint_score
                    best_total_detail = joint_total_detail
                    best_baseline = joint_baseline
                    best_img = joint_img
                    best_values = joint_values.copy()
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

            self._finalize_strategy_iteration(
                it,
                iteration_strategy_stats,
                improved_in_iter=improved_in_iter,
            )

            if best_score <= self.target_score:
                print("Target score reached.")
                break

            if not improved_in_iter and all(p.step <= p.min_step + 1e-12 for p in self.params):
                stop_reason = "all_steps_minimum"
                print("No further improvement and all steps at min_step. Stop.")
                break

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

    def optimize(self) -> dict:
        if self.optimizer_mode == "bayesian":
            return self._optimize_bayesian()
        elif self.optimizer_mode == "auto":
            if _OPTUNA_AVAILABLE:
                print("Using Bayesian optimizer (optuna available)")
                return self._optimize_bayesian()
            else:
                print("Using coordinate_descent optimizer (optuna not available)")
                return self._optimize_coordinate_descent()
        else:
            return self._optimize_coordinate_descent()



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IPGMovie camera calibration multi-board matching loop"
    )
    parser.add_argument(
        "--precheck",
        action="store_true",
        help="Run camera precheck (check raw images, configs) and exit",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="CarMaker project root (for precheck mode)",
    )
    parser.add_argument(
        "--camera",
        action="append",
        dest="cameras",
        default=[],
        help="Camera sensor name to precheck. Repeat for multiple cameras.",
    )
    parser.add_argument(
        "--config",
        required=False,
        help="Path to runtime JSON config, for example configs/camera.rear_tv.json; required except in bootstrap mode",
    )
    parser.add_argument(
        "--capture-initials",
        action="store_true",
        help="Read current Script Control values and print initial values",
    )
    parser.add_argument(
        "--propose-boards",
        action="store_true",
        help="Auto-detect candidate board instances from real_image and write a proposed config",
    )
    parser.add_argument(
        "--proposal-output",
        default=None,
        help="Optional path for proposed config output",
    )
    parser.add_argument(
        "--proposal-preview",
        default=None,
        help="Optional path for proposal preview image output",
    )
    parser.add_argument(
        "--bootstrap-config-from-annotation",
        action="store_true",
        help="Generate a new camera config from a real image plus a manually annotated red-box image",
    )
    parser.add_argument(
        "--bootstrap-real-image",
        default=None,
        help="Real camera image used as the new config real_image",
    )
    parser.add_argument(
        "--bootstrap-template-config",
        default=None,
        help="Path to standalone bootstrap template input; defaults to configs/bootstrap.template.json next to the script",
    )
    parser.add_argument(
        "--bootstrap-annotated-image",
        default=None,
        help="Manually annotated image with red rectangles around boards",
    )
    parser.add_argument(
        "--bootstrap-output",
        default=None,
        help="Optional output path for the generated config",
    )
    parser.add_argument(
        "--bootstrap-preview",
        default=None,
        help="Optional output path for the generated preview image",
    )
    parser.add_argument(
        "--bootstrap-camera-name",
        default=None,
        help="Optional camera name override for generated config/output naming",
    )
    parser.add_argument(
        "--bootstrap-skip-current-params",
        action="store_true",
        help="Skip reading current window parameters through Script Control during config bootstrap",
    )
    parser.add_argument(
        "--annotate-image",
        default=None,
        help="Annotate an existing simulation image using the current config",
    )
    parser.add_argument(
        "--annotate-output",
        default=None,
        help="Optional output path for --annotate-image",
    )
    parser.add_argument(
        "--multi-start-count",
        type=int,
        default=0,
        help="Run multiple optimizations from perturbed config initial values; 0 disables this mode",
    )
    parser.add_argument(
        "--multi-start-iters",
        type=int,
        default=None,
        help="Optional max_iters override for each multi-start run",
    )
    parser.add_argument(
        "--multi-start-jitter-steps",
        type=float,
        default=2.0,
        help="Perturb each unlocked parameter by up to N * step around config initial values",
    )
    parser.add_argument(
        "--multi-start-seed",
        type=int,
        default=20260429,
        help="Random seed for deterministic multi-start initial value generation",
    )
    parser.add_argument(
        "--explore-then-refine",
        action="store_true",
        help="Run a short multi-start exploration first, then launch one refinement run from the best explored start",
    )
    parser.add_argument(
        "--refine-iters",
        type=int,
        default=None,
        help="Optional max_iters override for the refinement phase of --explore-then-refine",
    )
    parser.add_argument(
        "--resume-from-result",
        action="store_true",
        help="Optional legacy mode: resume parameter values from the last result before optimize",
    )
    parser.add_argument(
        "--campaign-rounds",
        type=int,
        default=1,
        help="Repeat multi-start or explore-then-refine for N outer rounds, carrying previous best values and learned param order into the next round",
    )
    parser.add_argument(
        "--verbose-dde-diag",
        action="store_true",
        help="Print per-attempt DDE success diagnostics; retry/failed logs remain enabled by default",
    )
    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="Print one machine-readable JSON summary line on successful completion",
    )
    parser.add_argument(
        "--print-progress-json",
        action="store_true",
        help="Print machine-readable JSON progress lines whenever result.json is refreshed",
    )
    return parser.parse_args()


def _build_cli_summary_payload(
    *,
    camera_name: str,
    config_path: Path,
    mode: str,
    result_json_path: Optional[Path] = None,
    result_payload: Optional[dict] = None,
    summary_json_path: Optional[Path] = None,
    rounds_output_dir: Optional[Path] = None,
) -> dict:
    payload = result_payload or {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    best_score = payload.get("best_score")
    if best_score is None:
        best_score = summary.get("final_score")

    return {
        "camera": camera_name,
        "config_path": str(config_path),
        "mode": mode,
        "result_json": str(result_json_path) if result_json_path else None,
        "summary_json": str(summary_json_path) if summary_json_path else None,
        "rounds_output_dir": str(rounds_output_dir) if rounds_output_dir else None,
        "output_dir": payload.get("output_dir"),
        "in_progress": bool(payload.get("in_progress", False)),
        "best_score": best_score,
        "best_image": payload.get("best_image"),
        "best_score_image": payload.get("best_score_image"),
        "best_overlay_image": payload.get("best_overlay_image"),
        "current_iter_index": summary.get("current_iter_index"),
        "current_iter_score": summary.get("current_iter_score"),
        "final_score": summary.get("final_score"),
        "passed": summary.get("passed"),
        "stop_reason": payload.get("stop_reason") or summary.get("stop_reason"),
        "live_log": payload.get("live_log"),
        "run_session_id": payload.get("run_session_id"),
    }


def _emit_cli_summary_json(payload: dict) -> None:
    print("CALIBRATION_SUMMARY_JSON:", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _emit_cli_progress_json(payload: dict) -> None:
    print("CALIBRATION_PROGRESS_JSON:", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _auto_detect_cameras(project_root: Path) -> list[str]:
    config_dir = project_root / "Data" / "Script" / "CameraCalibration" / "configs"
    if not config_dir.is_dir():
        return []
    import re as _cam_re
    pattern = _cam_re.compile(r"^camera\.(.+)\.json$")
    names: list[str] = []
    for f in config_dir.iterdir():
        m = pattern.match(f.name)
        if m and not f.name.endswith(".bak.json"):
            names.append(m.group(1))
    return sorted(names)


def main() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True, write_through=True)
        sys.stderr.reconfigure(line_buffering=True, write_through=True)
    except Exception:
        pass

    args = parse_args()

    if args.multi_start_count < 0:
        raise ValueError("--multi-start-count must be >= 0")
    if args.multi_start_iters is not None and args.multi_start_iters <= 0:
        raise ValueError("--multi-start-iters must be > 0")
    if args.multi_start_jitter_steps < 0.0:
        raise ValueError("--multi-start-jitter-steps must be >= 0")
    if args.refine_iters is not None and args.refine_iters <= 0:
        raise ValueError("--refine-iters must be > 0")
    if args.campaign_rounds <= 0:
        raise ValueError("--campaign-rounds must be > 0")

    if args.precheck:
        root = args.project_root.resolve() if args.project_root else Path.cwd()
        cameras = args.cameras if args.cameras else _auto_detect_cameras(root)
        results = run_precheck(root, cameras)
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    if args.bootstrap_config_from_annotation:
        if not args.bootstrap_real_image or not args.bootstrap_annotated_image:
            raise ValueError(
                "--bootstrap-config-from-annotation requires --bootstrap-real-image and --bootstrap-annotated-image"
            )
        if args.multi_start_count > 0 or args.explore_then_refine or args.resume_from_result:
            raise ValueError(
                "bootstrap-config-from-annotation cannot be combined with optimization campaign options"
            )
        if args.propose_boards or args.annotate_image or args.capture_initials:
            raise ValueError(
                "bootstrap-config-from-annotation cannot be combined with capture/propose/annotate commands"
            )
        template_config_path = (
            Path(args.bootstrap_template_config).resolve()
            if args.bootstrap_template_config
            else _default_bootstrap_template_path()
        )
        bootstrap_config_from_annotation(
            template_config_path=template_config_path,
            real_image_path=Path(args.bootstrap_real_image),
            annotated_image_path=Path(args.bootstrap_annotated_image),
            output_path=Path(args.bootstrap_output) if args.bootstrap_output else None,
            preview_path=Path(args.bootstrap_preview) if args.bootstrap_preview else None,
            camera_name=args.bootstrap_camera_name,
            capture_current_params=not args.bootstrap_skip_current_params,
        )
        return

    if not args.config:
        raise ValueError("--config is required unless --bootstrap-config-from-annotation is used")

    config_path = Path(args.config).resolve()
    camera_name = _camera_name_from_config_path(config_path)
    with open(config_path, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    if args.verbose_dde_diag:
        cfg["verbose_dde_diag"] = True

    base_output_dir = _resolve_config_output_dir(cfg, config_path)
    cfg["output_dir"] = str(base_output_dir)
    should_optimize = not any(
        [
            args.propose_boards,
            bool(args.annotate_image),
            args.capture_initials,
        ]
    )
    requires_runtime_session = bool(args.capture_initials) or should_optimize

    if requires_runtime_session and should_optimize:
        print(f"Config initial values BEFORE vehicle DDE read for {camera_name}:")
        for name, param in sorted(cfg.get("parameters", {}).items()):
            if "initial" in param:
                print(f"  {name}: {param['initial']}")
            else:
                print(f"  {name}: (no initial)")
        _vehicle_initial_values = _read_vehicle_initial_values_mandatory(camera_name)
        print(f"Vehicle DDE read returned {len(_vehicle_initial_values)} values:")
        for name, value in sorted(_vehicle_initial_values.items()):
            print(f"  {name}: {value}")
        for name, value in _vehicle_initial_values.items():
            if name in cfg.get("parameters", {}):
                cfg["parameters"][name]["initial"] = value
            else:
                print(f"  WARNING: {name} from vehicle file not in config parameters")
        print(f"Config initial values AFTER vehicle DDE read for {camera_name}:")
        for name, param in sorted(cfg.get("parameters", {}).items()):
            if "initial" in param:
                print(f"  {name}: {param['initial']}")
            else:
                print(f"  {name}: (no initial)")

    if requires_runtime_session:
        _acquire_runtime_session_lock(base_output_dir, config_path)

    if args.explore_then_refine:
        if not should_optimize:
            raise ValueError("explore-then-refine mode cannot be combined with capture/propose/annotate commands")
        if args.resume_from_result:
            print("Explore-then-refine mode ignores --resume-from-result and always starts from config initial values.")
        campaign_start_count = args.multi_start_count or 4
        campaign_explore_iters = args.multi_start_iters or min(int(cfg.get("max_iters", 100)), 24)
        rounds_payload = _run_explore_then_refine_rounds(
            config_path=config_path,
            cfg=cfg,
            base_output_dir=base_output_dir,
            camera_name=camera_name,
            round_count=int(args.campaign_rounds),
            start_count=campaign_start_count,
            jitter_steps=float(args.multi_start_jitter_steps),
            seed=int(args.multi_start_seed),
            explore_max_iters=int(campaign_explore_iters),
            refine_max_iters=args.refine_iters,
        )
        best_round = rounds_payload["best_round"] or {}
        best_run = best_round.get("best_run") or {}
        print("Rounds summary JSON:", rounds_payload["summary_json"])
        print("Rounds output dir:", rounds_payload["rounds_output_dir"])
        print("Completed rounds:", rounds_payload["round_count_completed"])
        print("Best round index:", best_round.get("round_index"))
        print("Campaign best stage:", best_run["stage"])
        print("Campaign best score:", best_run["best_score"])
        print("Campaign best image:", best_run["best_image"])
        print("Campaign best result JSON:", best_run["result_json"])
        camera_history_summary_path, camera_history_summary = _write_camera_history_summary(camera_name)
        camera_history_summary_compact_path = _write_camera_history_summary_compact(
            camera_name,
            camera_history_summary,
        )
        _print_camera_history_summary(camera_history_summary, camera_history_summary_path)
        _print_camera_history_summary_compact(camera_history_summary_compact_path)
        if args.print_summary_json:
            best_result_json_path = Path(best_run["result_json"]).resolve()
            _emit_cli_summary_json(
                _build_cli_summary_payload(
                    camera_name=camera_name,
                    config_path=config_path,
                    mode="explore_then_refine_rounds",
                    result_json_path=best_result_json_path,
                    result_payload=_load_json_if_exists(best_result_json_path) or {},
                    summary_json_path=Path(rounds_payload["summary_json"]).resolve(),
                    rounds_output_dir=Path(rounds_payload["rounds_output_dir"]).resolve(),
                )
            )
        return

    if args.multi_start_count > 0:
        if not should_optimize:
            raise ValueError("multi-start mode cannot be combined with capture/propose/annotate commands")
        if args.resume_from_result:
            print("Multi-start mode ignores --resume-from-result and always starts from config initial values.")
        rounds_payload = _run_multi_start_rounds(
            config_path=config_path,
            cfg=cfg,
            base_output_dir=base_output_dir,
            camera_name=camera_name,
            round_count=int(args.campaign_rounds),
            start_count=args.multi_start_count,
            jitter_steps=float(args.multi_start_jitter_steps),
            seed=int(args.multi_start_seed),
            max_iters_override=args.multi_start_iters,
        )
        best_round = rounds_payload["best_round"] or {}
        best_run = best_round.get("best_run") or {}
        print("Rounds summary JSON:", rounds_payload["summary_json"])
        print("Rounds output dir:", rounds_payload["rounds_output_dir"])
        print("Completed rounds:", rounds_payload["round_count_completed"])
        print("Best round index:", best_round.get("round_index"))
        print("Multi-start best score:", best_run["best_score"])
        print("Multi-start best image:", best_run["best_image"])
        print("Multi-start best result JSON:", best_run["result_json"])
        camera_history_summary_path, camera_history_summary = _write_camera_history_summary(camera_name)
        camera_history_summary_compact_path = _write_camera_history_summary_compact(
            camera_name,
            camera_history_summary,
        )
        _print_camera_history_summary(camera_history_summary, camera_history_summary_path)
        _print_camera_history_summary_compact(camera_history_summary_compact_path)
        if args.print_summary_json:
            best_result_json_path = Path(best_run["result_json"]).resolve()
            _emit_cli_summary_json(
                _build_cli_summary_payload(
                    camera_name=camera_name,
                    config_path=config_path,
                    mode="multi_start_rounds",
                    result_json_path=best_result_json_path,
                    result_payload=_load_json_if_exists(best_result_json_path) or {},
                    summary_json_path=Path(rounds_payload["summary_json"]).resolve(),
                    rounds_output_dir=Path(rounds_payload["rounds_output_dir"]).resolve(),
                )
            )
        return

    marker_path: Optional[Path] = None
    marker_payload: Optional[dict] = None
    resume_result_path: Optional[Path] = None
    if should_optimize and args.campaign_rounds > 1:
        rounds_payload = _run_plain_optimize_rounds(
            config_path=config_path,
            cfg=cfg,
            base_output_dir=base_output_dir,
            camera_name=camera_name,
            round_count=int(args.campaign_rounds),
            resume_from_result=bool(args.resume_from_result),
        )
        best_round = rounds_payload["best_round"] or {}
        print("Rounds summary JSON:", rounds_payload["summary_json"])
        print("Rounds output dir:", rounds_payload["rounds_output_dir"])
        print("Completed rounds:", rounds_payload["round_count_completed"])
        print("Best round index:", best_round.get("round_index"))
        print("Best score:", best_round.get("best_score"))
        print("Best image:", best_round.get("best_image"))
        print("Best result JSON:", best_round.get("result_json"))
        camera_history_summary_path, camera_history_summary = _write_camera_history_summary(camera_name)
        camera_history_summary_compact_path = _write_camera_history_summary_compact(
            camera_name,
            camera_history_summary,
        )
        _print_camera_history_summary(camera_history_summary, camera_history_summary_path)
        _print_camera_history_summary_compact(camera_history_summary_compact_path)
        if args.print_summary_json:
            best_result_json_path = Path(best_round["result_json"]).resolve()
            _emit_cli_summary_json(
                _build_cli_summary_payload(
                    camera_name=camera_name,
                    config_path=config_path,
                    mode="plain_optimize_rounds",
                    result_json_path=best_result_json_path,
                    result_payload=_load_json_if_exists(best_result_json_path) or {},
                    summary_json_path=Path(rounds_payload["summary_json"]).resolve(),
                    rounds_output_dir=Path(rounds_payload["rounds_output_dir"]).resolve(),
                )
            )
        return

    if should_optimize:
        marker_path = _marker_path_for_output_dir(base_output_dir)
        if args.resume_from_result:
            resume_result_path = _read_latest_result_path(marker_path, base_output_dir)
        cfg["output_dir"] = str(_build_isolated_output_dir("run", camera_parent=base_output_dir.name))
        marker_payload = {
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": str(config_path),
            "base_output_dir": str(base_output_dir),
            "output_dir": str(cfg["output_dir"]),
            "max_iters": int(cfg.get("max_iters", 0)),
            "resume_from_result": bool(args.resume_from_result),
            "status": "starting",
        }
        _write_run_marker(marker_path, marker_payload)
    else:
        cfg["output_dir"] = str(base_output_dir)

    live_log_path = _configure_live_log(cfg, args.resume_from_result)
    print("Live log:", str(live_log_path))
    if should_optimize:
        print("Isolated output dir:", str(cfg["output_dir"]))

    if marker_path is not None and marker_payload is not None:
        marker_payload["status"] = "running"
        marker_payload["live_log"] = str(live_log_path)
        _write_run_marker(marker_path, marker_payload)

    calib = CameraCalibrator(cfg, config_path=config_path)
    calib.live_log_path = live_log_path
    setattr(calib, "print_progress_json", bool(args.print_progress_json))
    calib._calib_max_iters = int(cfg.get("max_iters", 0))
    calib._calib_round_index = 1
    calib._calib_round_count = 1
    calib._calib_overall_total_iters = int(cfg.get("max_iters", 0))
    calib._calib_phase = "explore"
    # DDE capture_initial_values 已移除：初始值只从 vehicle 文件获取。
    # 如果需要恢复 DDE 覆盖，取消下方注释：
    # if not args.resume_from_result and should_optimize:
    #     initial_values = calib.capture_initial_values()
    #     for p in calib.params:
    #         if p.name in initial_values:
    #             p.value = initial_values[p.name]
    #     for name, value in initial_values.items():
    #         if name in cfg.get("parameters", {}):
    #             cfg["parameters"][name]["initial"] = value
    try:
        if args.propose_boards:
            calib.propose_boards_config(
                args.config,
                output_path=args.proposal_output,
                preview_path=args.proposal_preview,
            )
            return

        if args.annotate_image:
            annotated_path, board_scores = calib.annotate_existing_image(
                Path(args.annotate_image),
                Path(args.annotate_output) if args.annotate_output else None,
            )
            print("Annotated image:", str(annotated_path))
            for score in board_scores:
                print(
                    f"{score.board_id}: score={score.total_score:.6f} compared={score.compared} "
                    f"failed_reason={score.failed_reason}"
                )
            return

        if args.capture_initials:
            values = calib.capture_initial_values()
            print("Captured current values from CarMaker GUI:")
            for name, value in sorted(values.items()):
                print(f"  {name}: {value}")
            print("Note: These values are not written to config file. Vehicle file is the single source of truth.")
            return

        if args.resume_from_result:
            calib.load_best_values_from_result(
                resume_result_path or (base_output_dir / "result.json")
            )

        result = calib.optimize()
        _write_best_values_to_vehicle_config(
            config_path,
            cfg,
            camera_name,
            float(result["best_score"]),
            result["best_values"],
        )
        if marker_path is not None and marker_payload is not None:
            marker_payload.update(
                {
                    "status": "finished",
                    "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "best_score": result["best_score"],
                    "best_values": result["best_values"],
                    "best_image": result["best_image"],
                    "result_json": str(Path(cfg["output_dir"]) / "result.json"),
                    "run_session_id": result.get("run_session_id"),
                }
            )
            _write_run_marker(marker_path, marker_payload)
        print("Best score:", result["best_score"])
        print("Best values:", result["best_values"])
        print("Best image:", result["best_image"])
        if result.get("best_score_image"):
            print("Best score image:", result["best_score_image"])
        if result.get("best_overlay_image"):
            print("Best overlay image:", result["best_overlay_image"])
        run_stats = result.get("run_stats") or {}
        if run_stats:
            print(
                "Run stats: "
                f"calibration_count={run_stats.get('calibration_count')} "
                f"total_elapsed={run_stats.get('total_elapsed_text')} "
                f"average_elapsed={run_stats.get('average_elapsed_text')}"
            )
        print("Result JSON:", str(Path(cfg["output_dir"]) / "result.json"))
        camera_history_summary_path, camera_history_summary = _write_camera_history_summary(camera_name)
        camera_history_summary_compact_path = _write_camera_history_summary_compact(
            camera_name,
            camera_history_summary,
        )
        _print_camera_history_summary(camera_history_summary, camera_history_summary_path)
        _print_camera_history_summary_compact(camera_history_summary_compact_path)
        if args.print_summary_json:
            result_json_path = (Path(cfg["output_dir"]) / "result.json").resolve()
            _emit_cli_summary_json(
                _build_cli_summary_payload(
                    camera_name=camera_name,
                    config_path=config_path,
                    mode="single_run",
                    result_json_path=result_json_path,
                    result_payload=result,
                )
            )
    except Exception as exc:
        if marker_path is not None and marker_payload is not None:
            marker_payload.update(
                {
                    "status": "failed",
                    "failed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "error": str(exc),
                }
            )
            _write_run_marker(marker_path, marker_payload)
        raise


if __name__ == "__main__":
    main()
