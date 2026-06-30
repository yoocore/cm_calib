from __future__ import annotations

import re
from pathlib import Path

from src.entry.portable_runtime import resolve_tool_root

class ConfigService:
    CAMERA_CONFIG_RE = re.compile(r"^camera\.(?P<name>.+)\.json$", re.IGNORECASE)
    BACKUP_CONFIG_SUFFIX = ".bak.json"

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.calibration_root = resolve_tool_root()
        self.config_dir = self.project_root / "Data" / "Script" / "CameraCalibration"

    def list_cameras(self) -> list[str]:
        if not self.config_dir.exists():
            return []
        cameras: list[str] = []
        for path in sorted(self.config_dir.glob("camera.*.json")):
            if path.name.lower().endswith(self.BACKUP_CONFIG_SUFFIX):
                continue
            match = self.CAMERA_CONFIG_RE.match(path.name)
            if not match:
                continue
            cameras.append(match.group("name"))
        return cameras

    def resolve_config_path(self, camera_name: str) -> Path:
        return (self.config_dir / f"camera.{camera_name}.json").resolve()
