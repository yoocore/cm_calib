"""Stateless utility functions for camera calibration."""

import copy
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, TextIO, Tuple

from src.calibration.calib_types import TotalScoreDetail


_MISSING = object()


def _unlink_if_exists(path: Path) -> None:
    for _ in range(3):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.05)


def _default_sim_output_root(project_root: Optional[Path] = None) -> Path:
    if project_root:
        return project_root / "SimOutput" / "calibration"
    return Path("C:/CM_Projects/CMO141_Calibration/SimOutput") / "calibration"


def _sim_output_root_legacy() -> Path:
    """Legacy SimOutput root for backward-compatible path resolution of old runs."""
    return Path("C:/CM_Projects/CMO141_Calibration/SimOutput")


def _deep_merge_dict(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


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


def _is_circle_grid_board_type(board_type: str) -> bool:
    return str(board_type).strip().lower() == "circle_grid"


def _is_aruco_grid_board_type(board_type: str) -> bool:
    return str(board_type).strip().lower() == "aruco_grid"


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


def _camera_name_from_output_dir(output_dir: Path, project_root: Optional[Path] = None) -> str:
    roots = [_sim_output_root_legacy()]
    if project_root is not None:
        roots.insert(0, _default_sim_output_root(project_root))
    for root in roots:
        try:
            relative_parts = output_dir.resolve().relative_to(root.resolve()).parts
            if relative_parts:
                return _canonical_camera_group_name(relative_parts[0])
        except Exception:
            continue
    return _canonical_camera_group_name(output_dir.name)


def _quantize_float(value: float, decimals: int) -> float:
    return float(f"{float(value):.{decimals}f}")


def _round_floats(obj, decimals: int = 2, skip_keys: set | None = None):
    if isinstance(obj, float):
        return round(obj, decimals)
    if isinstance(obj, dict):
        return {k: v if skip_keys and k in skip_keys else _round_floats(v, decimals, skip_keys) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, decimals, skip_keys) for v in obj]
    return obj


def _format_scalar_value_map(values: Dict[str, float]) -> str:
    ordered = []
    for name in sorted(values.keys()):
        ordered.append(f"{name}={values[name]:.2f}")
    return ", ".join(ordered)


_DEFAULT_BOUNDS_MULTIPLIER = 50.0


def _clamp_to_parameter_bounds(param_cfg: dict, value: float, initial_value: Optional[float] = None) -> float:
    min_value, max_value = _resolve_parameter_bounds(param_cfg, initial_value=initial_value)
    decimals = int(param_cfg.get("decimals", 4))
    min_value = round(min_value, decimals)
    max_value = round(max_value, decimals)
    return max(min_value, min(max_value, round(value, decimals)))


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


def _build_annotation_legend_lines(
    total_detail: TotalScoreDetail,
    boards: Optional[List] = None,
) -> List[str]:
    """Build legend lines showing each board's individual score."""
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


class _TeeStream:
    """Split a stream into two output streams."""
    def __init__(self, primary: TextIO, secondary: TextIO):
        self._primary = primary
        self._secondary = secondary

    def write(self, data: str) -> int:
        written = self._primary.write(data)
        self._secondary.write(data)
        self.flush()
        return written

    def flush(self) -> None:
        try:
            self._primary.flush()
        except Exception:
            pass
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
