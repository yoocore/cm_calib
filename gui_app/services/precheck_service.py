from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime_config_bootstrap import bootstrap_runtime_configs_for_cameras


class PrecheckService:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.calibration_root = self.project_root / "Data" / "Script" / "CameraCalibration"
        self.bootstrap_template_path = self.calibration_root / "configs" / "bootstrap.template.json"
        self.config_dir = self.calibration_root / "configs"
        self.movie_dir = self.project_root / "Movie"

    def run_for_cameras(self, camera_names: list[str]) -> list[dict[str, Any]]:
        bootstrap_ok, bootstrap_message = self._validate_bootstrap_template()
        results: list[dict[str, Any]] = []
        for camera_name in camera_names:
            raw_matches = self._find_movie_files(self.movie_dir, camera_name, require_origin=True)
            annotated_matches = self._find_movie_files(self.movie_dir, camera_name, require_origin=False)
            ok = bool(raw_matches) and bool(annotated_matches) and bootstrap_ok
            messages: list[str] = []
            if not raw_matches:
                messages.append("missing raw image with sensor name and origin marker")
            if not annotated_matches:
                messages.append("missing annotated image with sensor name")
            if not bootstrap_ok:
                messages.append(bootstrap_message)
            if not messages:
                raw_names = [Path(p).name for p in raw_matches]
                ann_names = [Path(p).name for p in annotated_matches]
                messages.append(f"原始图像: {', '.join(raw_names)}; 标注图像: {', '.join(ann_names)}")
            def _rel(p: Path) -> str:
                try:
                    return str(p.relative_to(self.project_root))
                except (ValueError, TypeError):
                    return str(p)

            results.append(
                {
                    "camera": camera_name,
                    "ok": ok,
                    "raw_matches": [_rel(p) for p in raw_matches],
                    "annotated_matches": [_rel(p) for p in annotated_matches],
                    "message": "; ".join(messages),
                }
            )
        return results

    def generate_configs_for_cameras(self, camera_names: list[str]) -> list[dict[str, Any]]:
        generated = bootstrap_runtime_configs_for_cameras(
            project_root=self.project_root,
            camera_names=camera_names,
            config_dir=self.config_dir,
            template_path=self.bootstrap_template_path,
            movie_dir=self.movie_dir,
            overwrite_existing=True,
            capture_current_params=False,
        )
        results: list[dict[str, Any]] = []
        for item in generated:
            def _rel(p: str) -> str:
                try:
                    return str(Path(p).relative_to(self.project_root))
                except (ValueError, TypeError):
                    return p

            action = str(item.get("action") or "generated")
            config_rel = _rel(str(item.get("config_path") or ""))
            backup_rel = _rel(str(item.get("backup_path") or ""))
            preview_rel = _rel(str(item.get("preview_path") or ""))
            raw_val = item.get("raw_image_path")
            raw_list: list[str] = raw_val if isinstance(raw_val, list) else ([str(raw_val)] if raw_val else [])
            ann_val = item.get("annotated_image_path")
            ann_list: list[str] = ann_val if isinstance(ann_val, list) else ([str(ann_val)] if ann_val else [])
            raw_rel = [_rel(p) for p in raw_list if p]
            ann_rel = [_rel(p) for p in ann_list if p]

            message_parts = [action]
            if config_rel:
                message_parts.append(f"config={config_rel}")
            if backup_rel:
                message_parts.append(f"backup={backup_rel}")
            results.append(
                {
                    "camera": str(item.get("camera") or ""),
                    "ok": True,
                    "raw_matches": raw_rel,
                    "annotated_matches": ann_rel,
                    "config_path": config_rel,
                    "backup_path": backup_rel,
                    "preview_path": preview_rel,
                    "message": "; ".join(part for part in message_parts if part),
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
