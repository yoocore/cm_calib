from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.gui_app.services.board_auto_detector import (
    DetectedBoard,
    DetectedTag,
    TagGrid,
    assign_checkerboard_ids,
    group_tags_into_grids,
)


_DEFAULT_PARAMETERS = {
    "pos_x": {"step": 0.002, "min_step": 0.001, "decimals": 4},
    "pos_y": {"step": 0.001, "min_step": 0.001, "decimals": 4},
    "pos_z": {"step": 0.001, "min_step": 0.001, "decimals": 4},
    "yaw": {"step": 0.015, "min_step": 0.002, "decimals": 4},
    "pitch": {"step": 0.03, "min_step": 0.002, "decimals": 4},
    "roll": {"step": 0.01, "min_step": 0.002, "decimals": 4},
    "lens_fov": {"step": 0.2, "min_step": 0.1, "decimals": 1},
    "lens_scale": {"step": 0.005, "min_step": 0.005, "decimals": 3, "bounds_multiplier": 0},
    "lens_offset_x": {"step": 0.01, "min_step": 0.01, "decimals": 2, "bounds_multiplier": 0},
    "lens_offset_y": {"step": 0.01, "min_step": 0.01, "decimals": 2, "bounds_multiplier": 0},
}


def _derive_camera_name(image_path: str) -> str:
    stem = Path(image_path).stem
    name = re.sub(r"^origin[_\-]?", "", stem)
    name = re.sub(r"[_\-]origin$", "", name)
    return name or stem


def _board_entry_from_detected(board: DetectedBoard) -> dict:
    x, y, w, h = board.bbox
    entry: dict = {
        "board_id": board.board_id,
        "board_type": board.board_type,
        "weight": board.weight,
        "critical": True,
        "roi": [x, y, w, h],
    }
    if board.board_size:
        entry["board_size"] = list(board.board_size)
    if board.tags:
        tag_ids = sorted(t.tag_id for t in board.tags)
        entry["tag_ids"] = tag_ids
    if board.board_type == "circle_grid":
        entry["grid_type"] = "symmetric"
    if board.board_type == "aruco_grid":
        entry["aruco_dictionary"] = "DICT_4X4_50"
        entry["square_size"] = 1.0
        entry["marker_separation"] = 0.5
    if board.board_type == "custom_maker":
        entry["custom_detector"] = "template_match"
        entry["template_match_threshold"] = 0.45
        entry["template_binary_threshold"] = 150
        entry["min_detected_points"] = 9
        if board.template_image:
            entry["template_image"] = board.template_image
    return entry


def _board_entry_from_tag_grid(grid: TagGrid, board_type: str, **extra: Any) -> dict:
    x, y, w, h = grid.bbox
    entry: dict = {
        "board_id": grid.grid_id,
        "board_type": board_type,
        "weight": 1.0,
        "critical": True,
        "roi": [x, y, w, h],
        "tag_ids": sorted(t.tag_id for t in grid.tags),
    }
    entry.update(extra)
    return entry


def generate_config(
    boards: List[DetectedBoard],
    tag_grids: Optional[List[TagGrid]],
    real_image_path: str,
    output_path: Path,
    template_config: Optional[dict] = None,
    camera_name: Optional[str] = None,
) -> dict:
    resolved_name = camera_name or _derive_camera_name(real_image_path)
    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg: Dict[str, Any] = {}
    if template_config:
        cfg = json.loads(json.dumps(template_config))
        cfg.pop("boards", None)
        cfg.pop("bootstrap_templates", None)

    cfg.setdefault("real_image", str(Path(real_image_path).resolve()))
    cfg.setdefault("settings_input_mode", "script_control")
    cfg.setdefault("comparison_mode", "direct")
    cfg.setdefault("keep_aspect_resize", True)
    cfg.setdefault("max_iters", 180)
    cfg.setdefault("min_improve", 5e-5)
    cfg.setdefault("step_decay", 0.7)
    cfg.setdefault("target_score", round(0.3 * max(1, len(boards)), 1))
    cfg.setdefault("parameters", _DEFAULT_PARAMETERS)
    cfg["output_dir"] = str(output_dir)

    board_entries: List[dict] = []

    for board in boards:
        if not board.board_id:
            continue
        board_entries.append(_board_entry_from_detected(board))

    if tag_grids:
        extra_fields: dict = {}
        for board in boards:
            if board.board_type == "apriltag":
                extra_fields["tag_family"] = board.tags[0].family if board.tags else "auto"
                break
            if board.board_type == "aruco":
                extra_fields["aruco_dictionary"] = board.tags[0].family if board.tags else "DICT_4X4_50"
                break

        for grid in tag_grids:
            if any(e["board_id"] == grid.grid_id for e in board_entries):
                continue
            board_type = boards[0].board_type if boards else "apriltag"
            board_entries.append(
                _board_entry_from_tag_grid(grid, board_type, **extra_fields)
            )

    cfg["boards"] = board_entries

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

    return cfg


def generate_preview_image(
    boards: List[DetectedBoard],
    tag_grids: Optional[List[TagGrid]],
    real_image_path: str,
    preview_path: Path,
) -> Optional[str]:
    try:
        import cv2
    except ImportError:
        return None

    img = cv2.imread(real_image_path)
    if img is None:
        return None

    colors = {
        "B": (70, 80, 230),
        "S": (60, 170, 90),
        "G": (220, 110, 60),
    }

    for board in boards:
        x, y, w, h = board.bbox
        prefix = board.board_id[:1] if board.board_id else "B"
        color = colors.get(prefix, (200, 200, 70))
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 3)
        cv2.putText(
            img, board.board_id,
            (x, max(24, y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA,
        )

    if tag_grids:
        for grid in tag_grids:
            if any(b.board_id == grid.grid_id for b in boards):
                continue
            x, y, w, h = grid.bbox
            color = colors.get("G", (220, 110, 60))
            cv2.rectangle(img, (x, y), (x + w, y + h), color, 3)
            cv2.putText(
                img, grid.grid_id,
                (x, max(24, y - 10)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA,
            )

    preview_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(preview_path), img)
    return str(preview_path)
