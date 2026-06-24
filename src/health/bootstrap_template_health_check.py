from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate bootstrap-generated custom templates so they do not silently shrink "
            "to a tiny texture patch instead of covering the intended annotated ROI."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to a generated camera.<camera>.json config",
    )
    parser.add_argument(
        "--min-width-ratio",
        type=float,
        default=0.95,
        help="Minimum acceptable template_source_crop width ratio relative to template_source_roi",
    )
    parser.add_argument(
        "--min-height-ratio",
        type=float,
        default=0.95,
        help="Minimum acceptable template_source_crop height ratio relative to template_source_roi",
    )
    parser.add_argument(
        "--min-area-ratio",
        type=float,
        default=0.90,
        help="Minimum acceptable template_source_crop area ratio relative to template_source_roi",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional directory for summary.json output",
    )
    return parser.parse_args()


def _parse_roi(value: object) -> Optional[Tuple[int, int, int, int]]:
    if not isinstance(value, list) or len(value) != 4:
        return None
    return tuple(int(item) for item in value)


def _camera_name_from_config_path(config_path: Path) -> str:
    stem = config_path.stem
    for prefix in ("camera.", "config."):
        if stem.startswith(prefix):
            return stem[len(prefix) :]
    return stem


def _default_output_dir(config_path: Path) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return Path(__file__).resolve().parents[2] / "SimOutput" / "bootstrap_template_health" / timestamp


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Config root must be a JSON object: {path}")
    return payload


def _is_bootstrap_auto_template(template_path: Path, camera_name: str) -> bool:
    parts = {part.lower() for part in template_path.parts}
    return "bootstrap_templates" in parts and camera_name.lower() in parts


def _read_template_size(path: Path) -> Optional[Tuple[int, int]]:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    height, width = image.shape[:2]
    return (width, height)


def _default_auto_template_path(real_image_path: Path, camera_name: str, board_id: str) -> Path:
    template_name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in board_id).strip("_")
    template_name = template_name or "custom_maker"
    return real_image_path.resolve().parent / "bootstrap_templates" / camera_name / f"{template_name.lower()}_auto.png"


def _build_check_record(
    board_cfg: dict,
    real_image_path: Path,
    camera_name: str,
    min_width_ratio: float,
    min_height_ratio: float,
    min_area_ratio: float,
) -> Dict[str, Any]:
    board_id = str(board_cfg.get("board_id", "")).strip()
    board_type = str(board_cfg.get("board_type", "")).strip().lower()
    roi = _parse_roi(board_cfg.get("roi"))
    source_roi = _parse_roi(board_cfg.get("template_source_roi")) or roi
    source_crop = _parse_roi(board_cfg.get("template_source_crop"))
    template_image_raw = str(board_cfg.get("template_image", "")).strip()
    template_image = Path(template_image_raw) if template_image_raw else None

    record: Dict[str, Any] = {
        "board_id": board_id,
        "board_type": board_type,
        "ok": True,
        "issues": [],
        "template_image": template_image_raw,
        "roi": list(roi) if roi else None,
        "template_source_roi": list(source_roi) if source_roi else None,
        "template_source_crop": list(source_crop) if source_crop else None,
    }

    if board_type != "custom_maker":
        record["skipped"] = True
        record["skip_reason"] = "board_type is not custom_maker"
        return record

    if source_roi is None:
        record["ok"] = False
        record["issues"].append("missing template_source_roi and roi")
        return record

    source_x, source_y, source_w, source_h = source_roi
    if source_w <= 0 or source_h <= 0:
        record["ok"] = False
        record["issues"].append("template_source_roi has non-positive size")
        return record

    if source_crop is None:
        source_crop = (0, 0, source_w, source_h)
        record["template_source_crop"] = list(source_crop)

    if template_image is None and source_roi is not None:
        template_image = _default_auto_template_path(real_image_path, camera_name, board_id)
        template_image_raw = str(template_image.resolve().as_posix())
        record["template_image"] = template_image_raw

    crop_x, crop_y, crop_w, crop_h = source_crop
    if crop_w <= 0 or crop_h <= 0:
        record["ok"] = False
        record["issues"].append("template_source_crop has non-positive size")
        return record

    width_ratio = crop_w / max(source_w, 1)
    height_ratio = crop_h / max(source_h, 1)
    area_ratio = (crop_w * crop_h) / max(source_w * source_h, 1)
    record["width_ratio"] = width_ratio
    record["height_ratio"] = height_ratio
    record["area_ratio"] = area_ratio
    record["crop_origin"] = [crop_x, crop_y]
    record["expected_full_source_crop"] = [0, 0, source_w, source_h]

    template_is_bootstrap_auto = template_image is not None and _is_bootstrap_auto_template(
        template_image.resolve(), camera_name
    )
    record["template_is_bootstrap_auto"] = bool(template_is_bootstrap_auto)

    if template_is_bootstrap_auto:
        if width_ratio < min_width_ratio:
            record["ok"] = False
            record["issues"].append(
                f"template_source_crop width ratio {width_ratio:.3f} < {min_width_ratio:.3f}"
            )
        if height_ratio < min_height_ratio:
            record["ok"] = False
            record["issues"].append(
                f"template_source_crop height ratio {height_ratio:.3f} < {min_height_ratio:.3f}"
            )
        if area_ratio < min_area_ratio:
            record["ok"] = False
            record["issues"].append(
                f"template_source_crop area ratio {area_ratio:.3f} < {min_area_ratio:.3f}"
            )
        if crop_x != 0 or crop_y != 0:
            record["ok"] = False
            record["issues"].append(
                "bootstrap auto template crop does not start at source ROI origin"
            )

    if template_image is None:
        record["ok"] = False
        record["issues"].append("missing template_image")
        return record

    if not template_image.exists():
        record["ok"] = False
        record["issues"].append(f"template_image does not exist: {template_image}")
        return record

    actual_template_size = _read_template_size(template_image)
    record["template_image_size"] = list(actual_template_size) if actual_template_size else None
    if actual_template_size is None:
        record["ok"] = False
        record["issues"].append(f"cannot read template_image: {template_image}")
        return record

    actual_w, actual_h = actual_template_size
    if abs(actual_w - crop_w) > 1 or abs(actual_h - crop_h) > 1:
        record["ok"] = False
        record["issues"].append(
            "template_image size does not match template_source_crop size"
        )

    return record


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    cfg = _load_json(config_path)
    boards = cfg.get("boards")
    if not isinstance(boards, list):
        raise RuntimeError(f"Config is missing boards[]: {config_path}")
    real_image_raw = str(cfg.get("real_image", "")).strip()
    if not real_image_raw:
        raise RuntimeError(f"Config is missing real_image: {config_path}")
    real_image_path = Path(real_image_raw)

    camera_name = _camera_name_from_config_path(config_path)
    output_dir = (args.output_dir or _default_output_dir(config_path)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []
    failed_records: List[Dict[str, Any]] = []
    checked_count = 0
    for board_cfg in boards:
        if not isinstance(board_cfg, dict):
            continue
        record = _build_check_record(
            board_cfg,
            real_image_path,
            camera_name,
            float(args.min_width_ratio),
            float(args.min_height_ratio),
            float(args.min_area_ratio),
        )
        records.append(record)
        if record.get("skipped"):
            continue
        checked_count += 1
        status = "ok" if record.get("ok") else "failed"
        ratio_text = (
            f"width_ratio={record.get('width_ratio', 0.0):.3f}, "
            f"height_ratio={record.get('height_ratio', 0.0):.3f}, "
            f"area_ratio={record.get('area_ratio', 0.0):.3f}"
        )
        print(f"Bootstrap template health [{record['board_id']}] status={status} {ratio_text}")
        if not record.get("ok"):
            failed_records.append(record)
            for issue in record.get("issues", []):
                print(f"  issue: {issue}")

    summary = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config": str(config_path),
        "camera": camera_name,
        "checked_custom_template_count": checked_count,
        "failed_count": len(failed_records),
        "ok": len(failed_records) == 0,
        "thresholds": {
            "min_width_ratio": float(args.min_width_ratio),
            "min_height_ratio": float(args.min_height_ratio),
            "min_area_ratio": float(args.min_area_ratio),
        },
        "records": records,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Bootstrap template health summary: {summary_path}")
    print(f"Bootstrap template health ok: {summary['ok']}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())