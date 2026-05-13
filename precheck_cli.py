#!/usr/bin/env python3
"""CLI entry point for camera precheck. Called by the GUI frontend via subprocess."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PRECHECK_RESULT_PREFIX = "PRECHECK_RESULT_JSON:"
_SENSOR_NAME_RE = re.compile(r"^\s*Sensor\.(?P<index>\d+)\.name\s*=\s*(?P<value>.+?)\s*$")
_SENSOR_ACTIVE_RE = re.compile(r"^\s*Sensor\.(?P<index>\d+)\.Active\s*=\s*(?P<value>[01])\s*$")
_PARAM_TYPE_RE = re.compile(r"^\s*Sensor\.Param\.(?P<index>\d+)\.Type\s*=\s*(?P<value>.+?)\s*$")


def _emit(payload: dict) -> None:
    print(f"{PRECHECK_RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}")


def _camera_param_indices(vehicle_path: Path) -> set[str]:
    text = vehicle_path.read_text(encoding="utf-8")
    indices: set[str] = set()
    for line in text.splitlines():
        m = _PARAM_TYPE_RE.match(line)
        if m and m.group("value").strip().casefold() == "camerarsi":
            indices.add(m.group("index"))
    return indices


def _read_vehicle_sensors(vehicle_path: Path) -> list[dict[str, Any]]:
    camera_indices = _camera_param_indices(vehicle_path)
    text = vehicle_path.read_text(encoding="utf-8")
    sensor_names: dict[str, str] = {}
    sensor_active: dict[str, bool] = {}
    for line in text.splitlines():
        m = _SENSOR_NAME_RE.match(line)
        if m:
            sensor_names[m.group("index")] = m.group("value").strip()
            continue
        m = _SENSOR_ACTIVE_RE.match(line)
        if m:
            sensor_active[m.group("index")] = m.group("value") == "1"
    sensors: list[dict[str, Any]] = []
    for idx in sorted(sensor_names, key=int):
        if idx not in camera_indices:
            continue
        sensors.append({
            "index": int(idx),
            "name": sensor_names[idx],
            "active": sensor_active.get(idx, False),
        })
    return sensors


def _find_movie_files(movie_dir: Path, camera_name: str, require_origin: bool) -> list[Path]:
    if not movie_dir.exists():
        return []
    matches: list[Path] = []
    camera_key = camera_name.casefold()
    for path in movie_dir.rglob("*"):
        if not path.is_file():
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
    return matches


def _validate_bootstrap_template(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"missing bootstrap template: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return False, f"invalid bootstrap template JSON: {exc}"
    templates = payload.get("bootstrap_templates")
    if not isinstance(templates, list) or not templates:
        return False, "bootstrap template does not contain bootstrap_templates"
    return True, "ok"


def run_precheck(project_root: Path, cameras: list[str]) -> list[dict[str, Any]]:
    movie_dir = project_root / "Movie"
    config_dir = project_root / "Data" / "Script" / "CameraCalibration" / "configs"
    bootstrap_path = config_dir / "bootstrap.template.json"

    bootstrap_ok, bootstrap_msg = _validate_bootstrap_template(bootstrap_path)

    # Also read Vehicle sensors for validation
    testrun_dir = project_root / "Data" / "TestRun"
    vehicle_sensors: list[str] = []
    if testrun_dir.is_dir():
        for testrun_file in testrun_dir.iterdir():
            if testrun_file.is_file():
                try:
                    text = testrun_file.read_text(encoding="utf-8")
                    for line in text.splitlines():
                        m = re.match(r"^\s*Vehicle\s*=\s*(?P<value>.+?)\s*$", line)
                        if m:
                            vkey = m.group("value").strip()
                            vpath = project_root / "Data" / "Vehicle" / Path(vkey.replace("\\", "/"))
                            if vpath.exists():
                                vehicle_sensors = [s["name"] for s in _read_vehicle_sensors(vpath)]
                            break
                except Exception:
                    pass
                break

    results: list[dict[str, Any]] = []
    for camera_name in cameras:
        raw_matches = _find_movie_files(movie_dir, camera_name, require_origin=True)
        ann_matches = _find_movie_files(movie_dir, camera_name, require_origin=False)
        ok = bool(raw_matches) and bootstrap_ok
        messages: list[str] = []
        if not raw_matches:
            messages.append("missing raw image with sensor name and origin marker")
        if not bootstrap_ok:
            messages.append(bootstrap_msg)
        raw_names = [p.name for p in raw_matches]
        ann_names = [p.name for p in ann_matches]
        parts = [f"原始图像: {', '.join(raw_names)}"] if raw_names else []
        if ann_names:
            parts.append(f"标注图像: {', '.join(ann_names)}")
        messages.append("; ".join(parts) if parts else "无检测结果")

        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(project_root))
            except (ValueError, TypeError):
                return str(p)

        config_path = config_dir / f"camera.{camera_name}.json"
        config_info = str(_rel(config_path)) if config_path.exists() else ""
        backup_files = sorted(config_dir.glob(f"camera.{camera_name}*.bak.json"))
        backup_info = str(_rel(backup_files[-1])) if backup_files else ""

        result = {
            "camera": camera_name,
            "ok": ok,
            "message": "; ".join(messages),
            "raw_matches": [_rel(p) for p in raw_matches],
            "annotated_matches": [_rel(p) for p in ann_matches],
            "config_path": config_info,
            "backup_path": backup_info,
            "preview_path": "",
            "vehicle_has_sensor": camera_name in vehicle_sensors,
        }
        _emit(result)
        results.append(result)
    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Camera precheck CLI")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--camera", action="append", dest="cameras", default=[])
    args = parser.parse_args()
    run_precheck(args.project_root.resolve(), args.cameras)


if __name__ == "__main__":
    main()
