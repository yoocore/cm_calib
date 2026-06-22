#!/usr/bin/env python3
"""CLI entry point for camera precheck. Called by the GUI frontend via subprocess."""
from __future__ import annotations

import json
import os
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




def run_precheck(project_root: Path, cameras: list[str]) -> list[dict[str, Any]]:
    mapping_mp = project_root / "Movie" / "calibtool_camera_config.json"
    mapping: dict = {}
    if mapping_mp.exists():
        mapping = json.loads(mapping_mp.read_text(encoding="utf-8"))

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
        messages: list[str] = []
        entry = mapping.get(camera_name, {})
        in_mapping = bool(entry)
        config_folder = entry.get("config_folder", "")
        config_folder_exists = bool(config_folder) and os.path.isdir(config_folder)
        real_image = entry.get("real_image", "")
        real_image_exists = bool(real_image) and os.path.isfile(real_image)

        config_json_exists = False
        config_has_boards = False
        if config_folder_exists:
            config_json_path = Path(config_folder) / f"camera.{camera_name}.json"
            config_json_exists = config_json_path.exists()
            if config_json_exists:
                try:
                    cfg = json.loads(config_json_path.read_text(encoding="utf-8-sig"))
                    boards = cfg.get("boards", []) or []
                    tag_grids = cfg.get("tag_grids", []) or []
                    config_has_boards = bool(boards) or bool(tag_grids)
                except (json.JSONDecodeError, Exception):
                    config_json_exists = False

        if not in_mapping:
            messages.append("camera not in mapping (run Wizard first)")
        elif not config_folder:
            messages.append("mapping entry missing config_folder")
        elif not config_folder_exists:
            messages.append(f"config folder not found: {config_folder}")
        elif not config_json_exists:
            messages.append(f"config JSON not found in {config_folder}")
        elif not config_has_boards:
            messages.append("config JSON has no boards defined")

        if real_image and not real_image_exists:
            messages.append(f"reference image not found: {real_image}")

        ok = (
            in_mapping
            and config_folder_exists
            and config_json_exists
            and config_has_boards
        )

        result = {
            "camera": camera_name,
            "ok": ok,
            "message": "; ".join(messages) if messages else "ok",
            "in_mapping": in_mapping,
            "config_folder": config_folder,
            "config_folder_exists": config_folder_exists,
            "config_json_exists": config_json_exists,
            "config_has_boards": config_has_boards,
            "real_image": real_image,
            "real_image_exists": real_image_exists,
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
