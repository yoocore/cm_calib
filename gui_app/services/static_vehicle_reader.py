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
_PARAM_NAME_RE = re.compile(
    r"^\s*Sensor\.Param\.(?P<index>\d+)\.Name\s*=\s*(?P<value>.+?)\s*$"
)


def _normalize(name: str) -> str:
    return re.sub(r"[_\s]+", "", name).casefold()


def _split_words(name: str) -> set[str]:
    parts = re.split(r"[_\s]+", name)
    words: set[str] = set()
    for part in parts:
        sub = re.sub(r"([a-z])([A-Z])", r"\1 \2", part).strip()
        sub = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", sub).strip()
        for token in sub.split():
            token = token.casefold()
            if len(token) >= 2:
                words.add(token)
    return words


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


def _parse_camera_param_names(vehicle_path: Path) -> set[str]:
    text = vehicle_path.read_text(encoding="utf-8")
    param_types: dict[str, str] = {}
    param_names: dict[str, str] = {}
    for line in text.splitlines():
        type_m = _PARAM_TYPE_RE.match(line)
        if type_m:
            param_types[type_m.group("index")] = type_m.group("value").strip()
            continue
        name_m = _PARAM_NAME_RE.match(line)
        if name_m:
            param_names[name_m.group("index")] = name_m.group("value").strip()
    camera_names: set[str] = set()
    for idx, ptype in param_types.items():
        if ptype.casefold() == "camerarsi" and idx in param_names:
            camera_names.add(param_names[idx])
    return camera_names


def _is_camera_sensor(sensor_name: str, camera_param_names: set[str]) -> bool:
    sensor_norm = _normalize(sensor_name)
    sensor_words = _split_words(sensor_name)
    for cname in camera_param_names:
        cname_norm = _normalize(cname)
        if sensor_norm == cname_norm:
            return True
        if sensor_norm in cname_norm or cname_norm in sensor_norm:
            return True
        cname_words = _split_words(cname)
        if sensor_words & cname_words:
            return True
        if len(sensor_name) <= 3 and sensor_name.isalpha() and sensor_name.isupper():
            abbrev = sensor_name.casefold()
            pos = 0
            for ch in abbrev:
                idx = cname_norm.find(ch, pos)
                if idx == -1:
                    break
                pos = idx + 1
            else:
                return True
    return False


def read_vehicle_sensors(vehicle_path: Path) -> list[dict[str, Any]]:
    camera_param_names = _parse_camera_param_names(vehicle_path)
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
        sname = sensor_names[idx]
        if not _is_camera_sensor(sname, camera_param_names):
            continue
        sensors.append({
            "index": int(idx),
            "name": sname,
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
