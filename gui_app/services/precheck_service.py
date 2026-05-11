from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PrecheckService:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.calibration_root = self.project_root / "Data" / "Script" / "CameraCalibration"
        self.bootstrap_template_path = self.calibration_root / "configs" / "bootstrap.template.json"

    def run_for_cameras(self, camera_names: list[str]) -> list[dict[str, Any]]:
        movie_dir = self.project_root / "Movie"
        bootstrap_ok, bootstrap_message = self._validate_bootstrap_template()
        results: list[dict[str, Any]] = []
        for camera_name in camera_names:
            raw_matches = self._find_movie_files(movie_dir, camera_name, require_origin=True)
            annotated_matches = self._find_movie_files(movie_dir, camera_name, require_origin=False)
            ok = bool(raw_matches) and bool(annotated_matches) and bootstrap_ok
            messages: list[str] = []
            if not raw_matches:
                messages.append("missing raw image with sensor name and origin marker")
            if not annotated_matches:
                messages.append("missing annotated image with sensor name")
            if not bootstrap_ok:
                messages.append(bootstrap_message)
            if not messages:
                messages.append("ok")
            results.append(
                {
                    "camera": camera_name,
                    "ok": ok,
                    "raw_matches": [str(path) for path in raw_matches],
                    "annotated_matches": [str(path) for path in annotated_matches],
                    "message": "; ".join(messages),
                }
            )
        return results

    def _validate_bootstrap_template(self) -> tuple[bool, str]:
        if not self.bootstrap_template_path.exists():
            return False, f"missing bootstrap template: {self.bootstrap_template_path}"
        try:
            payload = json.loads(self.bootstrap_template_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            return False, f"invalid bootstrap template JSON: {exc}"
        templates = payload.get("bootstrap_templates")
        if not isinstance(templates, list) or not templates:
            return False, "bootstrap template does not contain bootstrap_templates"
        return True, "ok"

    @staticmethod
    def _find_movie_files(movie_dir: Path, camera_name: str, *, require_origin: bool) -> list[Path]:
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
