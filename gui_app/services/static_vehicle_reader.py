from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_VEHICLE_RE = re.compile(r"^\s*Vehicle\s*=\s*(?P<value>.+?)\s*$")
_SENSOR_NAME_RE = re.compile(
    r"^\s*Sensor\.(?P<index>\d+)\.name\s*=\s*(?P<value>.+?)\s*$"
)
_SENSOR_ACTIVE_RE = re.compile(
    r"^\s*Sensor\.(?P<index>\d+)\.Active\s*=\s*(?P<value>[01])\s*$"
)
_PARAM_TYPE_RE = re.compile(
    r"^\s*Sensor\.Param\.(?P<index>\d+)\.Type\s*=\s*(?P<value>.+?)\s*$"
)


def parse_testrun_for_vehicle(testrun_path: Path) -> str:
    text = testrun_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = _VEHICLE_RE.match(line)
        if m:
            return m.group("value").strip()
    raise ValueError(
        f"TestRun file {testrun_path} does not contain a 'Vehicle =' entry"
    )


def build_vehicle_path(project_root: Path, vehicle_key: str) -> Path:
    vehicle_rel = Path(vehicle_key.replace("\\", "/"))
    return (project_root / "Data" / "Vehicle" / vehicle_rel).resolve()


def _camera_param_indices(vehicle_path: Path) -> set[str]:
    text = vehicle_path.read_text(encoding="utf-8")
    indices: set[str] = set()
    for line in text.splitlines():
        m = _PARAM_TYPE_RE.match(line)
        if m and m.group("value").strip().casefold() == "camerarsi":
            indices.add(m.group("index"))
    return indices


def read_vehicle_sensors(vehicle_path: Path) -> list[dict[str, Any]]:
    camera_indices = _camera_param_indices(vehicle_path)
    text = vehicle_path.read_text(encoding="utf-8")
    sensor_names: dict[str, str] = {}
    sensor_active: dict[str, bool] = {}
    for line in text.splitlines():
        name_m = _SENSOR_NAME_RE.match(line)
        if name_m:
            sensor_names[name_m.group("index")] = name_m.group("value").strip()
            continue
        active_m = _SENSOR_ACTIVE_RE.match(line)
        if active_m:
            sensor_active[active_m.group("index")] = active_m.group("value") == "1"
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


def resolve_vehicle_info(
    project_root: Path, testrun_rel: str
) -> dict[str, Any]:
    testrun_path = (project_root / "Data" / "TestRun" / testrun_rel).resolve()
    vehicle_key = parse_testrun_for_vehicle(testrun_path)
    vehicle_path = build_vehicle_path(project_root, vehicle_key)
    sensors = read_vehicle_sensors(vehicle_path)
    return {
        "vehicle_key": vehicle_key,
        "vehicle_path": str(vehicle_path),
        "sensors": sensors,
    }
