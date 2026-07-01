"""Configuration bootstrap, template handling, and annotation OCR for camera calibration."""

import copy
import json
import re
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.calibration.calib_types import ParameterSpec, BoardProfile
from src.calibration.utils import (
    _board_prototype_family,
    _bootstrap_partial_template_dir,
    _derive_camera_name_from_image_path,
    _deep_merge_dict,
    _is_aruco_family_board_type,
    _is_aruco_grid_board_type,
    _is_apriltag_board_type,
    _is_circle_grid_board_type,
    _is_custom_marker_board_type,
)




_ANNOTATION_OCR_ENGINE = None


def _default_bootstrap_template_path() -> Path:
    calibration_dir = Path(__file__).resolve().parent
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
    repo_root = Path(__file__).resolve().parents[2]
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
        "settle_sec": 0.1,
        "target_score": 5.0,
        "acceptance_criteria": {
            "bottleneck_board_score_max_threshold": 4.0,
            "bottleneck_board_score_avg_threshold": 2.5,
        },
        "max_iters": 180,
        "min_improve": 0.05,
        "step_decay": 0.85,
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
            "enabled": True,
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
        "curriculum": {
            "enabled": True,
            "phases": [
                {"progress_max": 0.50, "active_params": ["lens_fov", "lens_offset_x", "lens_offset_y"]},
                {"progress_max": 0.80, "active_params": ["lens_fov", "lens_offset_x", "lens_offset_y", "yaw", "pitch"]},
                {"progress_max": 1.00, "active_params": None},
            ],
        }
    }



def _resolved_bootstrap_config(template_cfg: dict) -> dict:
    cfg = _deep_merge_dict(_default_bootstrap_config(), template_cfg)
    return cfg


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
    from src.calibration.camera_calibration import CameraCalibrator
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
    from src.calibration.camera_calibration import CameraCalibrator
    bootstrap_calibrator = CameraCalibrator(cfg, config_path=output_file)
    _sync_materialized_board_fields_from_calibrator(cfg, bootstrap_calibrator)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

    preview_image = cv2.imread(str(resolved_real_image))
    if preview_image is None:
        raise FileNotFoundError(f"Failed to read real image: {resolved_real_image}")
    palette = {
        "CB": (70, 80, 230),
        "B": (70, 80, 230),
        "S": (60, 170, 90),
        "MK": (60, 170, 90),
        "C": (60, 170, 90),
        "CG": (180, 60, 180),
        "AG": (60, 180, 180),
        "AR": (180, 180, 60),
        "AT": (120, 60, 180),
        "G1": (220, 110, 60),
    }
    for board_cfg in generated_boards:
        x, y, width, height = [int(value) for value in board_cfg["roi"]]
        family = _board_prototype_family(str(board_cfg.get("board_id", ""))) or "CB"
        family_prefix = "G1" if family and family.startswith("G1") else (family or "CB")
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
