from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import cv2

from camera_calibration import CameraCalibrator, bootstrap_config_from_annotation


IMAGE_SUFFIXES = {".bmp", ".dib", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def _normalize_camera_names(camera_names: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in camera_names:
        camera_name = str(raw_name).strip()
        if not camera_name:
            continue
        key = camera_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(camera_name)
    return normalized


def _find_movie_files(movie_dir: Path, camera_name: str, *, require_origin: bool) -> list[Path]:
    if not movie_dir.exists():
        return []

    camera_key = camera_name.casefold()
    matches: list[Path] = []
    for path in movie_dir.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        stem = path.stem.casefold()
        if camera_key not in stem:
            continue
        has_origin = "origin" in stem
        if require_origin and not has_origin:
            continue
        if not require_origin and has_origin:
            continue
        matches.append(path)

    return sorted(
        matches,
        key=lambda path: (-path.stat().st_mtime, path.name.casefold()),
    )


def _read_image_size(image_path: Path) -> tuple[int, int]:
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Failed to read bootstrap source image: {image_path}")
    height, width = image.shape[:2]
    return int(width), int(height)


def load_movie_view_size_from_config(config_path: Path) -> tuple[int, int]:
    resolved_config_path = config_path.resolve()
    with resolved_config_path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)

    raw_real_image = str(payload.get("real_image") or "").strip()
    if not raw_real_image:
        raise ValueError(f"Config {resolved_config_path} must define real_image")

    real_image_path = Path(raw_real_image)
    if not real_image_path.is_absolute():
        real_image_path = (resolved_config_path.parent / real_image_path).resolve()
    else:
        real_image_path = real_image_path.resolve()

    return _read_image_size(real_image_path)


def _build_backup_path(config_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = config_path.with_name(f"{config_path.stem}.prepare.{timestamp}.bak{config_path.suffix}")
    counter = 1
    while candidate.exists():
        candidate = config_path.with_name(
            f"{config_path.stem}.prepare.{timestamp}_{counter}.bak{config_path.suffix}"
        )
        counter += 1
    return candidate


def bootstrap_runtime_config(
    *,
    project_root: Path,
    camera_name: str,
    config_dir: Path,
    template_path: Path,
    movie_dir: Optional[Path] = None,
    overwrite_existing: bool = True,
    capture_current_params: bool = False,
) -> dict[str, Any]:
    resolved_project_root = project_root.resolve()
    resolved_config_dir = config_dir.resolve()
    resolved_template_path = template_path.resolve()
    resolved_movie_dir = (movie_dir or (resolved_project_root / "Movie")).resolve()
    resolved_config_dir.mkdir(parents=True, exist_ok=True)

    raw_matches = _find_movie_files(resolved_movie_dir, camera_name, require_origin=True)
    annotated_matches = _find_movie_files(resolved_movie_dir, camera_name, require_origin=False)
    if not raw_matches:
        raise FileNotFoundError(f"No raw Movie image matched camera {camera_name!r} in {resolved_movie_dir}")
    if not annotated_matches:
        raise FileNotFoundError(f"No annotated Movie image matched camera {camera_name!r} in {resolved_movie_dir}")

    raw_image_path = raw_matches[0].resolve()
    annotated_image_path = annotated_matches[0].resolve()
    config_path = (resolved_config_dir / f"camera.{camera_name}.json").resolve()

    if config_path.exists() and not overwrite_existing:
        width, height = _read_image_size(raw_image_path)
        return {
            "camera": camera_name,
            "action": "reused_existing",
            "config_path": str(config_path),
            "backup_path": None,
            "raw_image_path": str(raw_image_path),
            "annotated_image_path": str(annotated_image_path),
            "raw_match_count": len(raw_matches),
            "annotated_match_count": len(annotated_matches),
            "movie_view_width": width,
            "movie_view_height": height,
        }

    backup_path: Optional[Path] = None
    action = "generated"
    if config_path.exists():
        backup_path = _build_backup_path(config_path)
        shutil.copy2(config_path, backup_path)
        action = "regenerated"

    generated_config_path, preview_path, generated_boards = bootstrap_config_from_annotation(
        template_config_path=resolved_template_path,
        real_image_path=raw_image_path,
        annotated_image_path=annotated_image_path,
        output_path=config_path,
        camera_name=camera_name,
        capture_current_params=capture_current_params,
    )
    width, height = _read_image_size(raw_image_path)

    return {
        "camera": camera_name,
        "action": action,
        "config_path": str(generated_config_path),
        "backup_path": str(backup_path) if backup_path is not None else None,
        "preview_path": str(preview_path),
        "raw_image_path": str(raw_image_path),
        "annotated_image_path": str(annotated_image_path),
        "raw_match_count": len(raw_matches),
        "annotated_match_count": len(annotated_matches),
        "generated_board_count": len(generated_boards),
        "movie_view_width": width,
        "movie_view_height": height,
    }


def bootstrap_runtime_configs_for_cameras(
    *,
    project_root: Path,
    camera_names: Iterable[str],
    config_dir: Path,
    template_path: Path,
    movie_dir: Optional[Path] = None,
    overwrite_existing: bool = True,
    capture_current_params: bool = False,
) -> list[dict[str, Any]]:
    return [
        bootstrap_runtime_config(
            project_root=project_root,
            camera_name=camera_name,
            config_dir=config_dir,
            template_path=template_path,
            movie_dir=movie_dir,
            overwrite_existing=overwrite_existing,
            capture_current_params=capture_current_params,
        )
        for camera_name in _normalize_camera_names(camera_names)
    ]


def capture_initial_values_to_config(config_path: Path) -> dict[str, Any]:
    resolved_config_path = config_path.resolve()
    with resolved_config_path.open("r", encoding="utf-8-sig") as handle:
        cfg = json.load(handle)

    calibrator = CameraCalibrator(cfg, config_path=resolved_config_path)
    values = calibrator.capture_initial_values()
    calibrator.write_initial_values_to_config(str(resolved_config_path), values)
    return {
        "config_path": str(resolved_config_path),
        "captured_names": sorted(values.keys()),
        "captured_values": values,
    }