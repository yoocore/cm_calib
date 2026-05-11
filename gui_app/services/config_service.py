from __future__ import annotations

import re
from pathlib import Path


class ConfigService:
    CAMERA_CONFIG_RE = re.compile(r"^camera\.(?P<name>.+)\.json$", re.IGNORECASE)

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.calibration_root = self.project_root / "Data" / "Script" / "CameraCalibration"
        self.config_dir = self.calibration_root / "configs"

    def list_cameras(self) -> list[str]:
        if not self.config_dir.exists():
            return []
        cameras: list[str] = []
        for path in sorted(self.config_dir.glob("camera.*.json")):
            match = self.CAMERA_CONFIG_RE.match(path.name)
            if not match:
                continue
            cameras.append(match.group("name"))
        return cameras

    def resolve_config_path(self, camera_name: str) -> Path:
        return (self.config_dir / f"camera.{camera_name}.json").resolve()
