from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcessEnvironment, Signal

from gui_app.services.process_service import ProcessService


def _resolve_cm_python(cm_install: Path) -> Path | None:
    candidates = [
    cm_install / "Python" / "python.exe",
    cm_install / "Python" / "python",
    cm_install.parent / "Python" / "python.exe",
    cm_install / "bin" / "python.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


class RuntimeService(QObject):
    line_received = Signal(str)
    runtime_summary = Signal(dict)
    process_started = Signal()
    process_finished = Signal(int)
    process_failed = Signal(str)

    def __init__(self, project_root: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.calibration_root = self.project_root / "Data" / "Script" / "CameraCalibration"
        self.process_service = ProcessService(self)
        self.process_service.line_received.connect(self.line_received.emit)
        self.process_service.runtime_summary.connect(self.runtime_summary.emit)
        self.process_service.process_started.connect(self.process_started.emit)
        self.process_service.process_finished.connect(self.process_finished.emit)
        self.process_service.process_failed.connect(self.process_failed.emit)

    @property
    def is_running(self) -> bool:
        return self.process_service.is_running

    def probe_status(self, project_root: Path, testrun: str, *, verify_health: bool = False) -> None:
        self._start_mode("status", project_root, testrun, verify_health=verify_health)

    def prepare_runtime(self, project_root: Path, testrun: str, *, cameras: list[str] | None = None, cm_install: Path | None = None) -> None:
        self._start_mode("prepare", project_root, testrun, cameras=cameras, cm_install=cm_install)

    def stop(self) -> None:
        self.process_service.stop()

    @staticmethod
    def _resolve_calibration_root(project_root: Path) -> Path:
        return project_root / "Data" / "Script" / "CameraCalibration"

    def _start_mode(
        self,
        mode: str,
        project_root: Path,
        testrun: str,
        *,
        verify_health: bool = False,
        cameras: list[str] | None = None,
        cm_install: Path | None = None,
    ) -> None:
        calibration_root = self._resolve_calibration_root(project_root)
        script_path = calibration_root / "cmapi_testrun_control.py"
        arguments = [
            str(script_path),
            "--mode",
            mode,
            "--project-root",
            str(project_root.resolve()),
            "--testrun",
            testrun,
            "--print-summary-json",
        ]
        if mode == "status" and verify_health:
            arguments.append("--health-check-after-start")
        if mode == "prepare":
            for camera_name in cameras or []:
                arguments.extend(["--camera-sensor", camera_name])
        if cm_install is not None:
            arguments.extend(["--cm-install", str(cm_install)])
        if cm_install is not None:
            for _sub in ("Python/Lib/site-packages", "Python/Lib", "pylib"):
                _p = cm_install / _sub
                if any((_p / c).exists() for c in ("cmapi", "cmapi.py", "cmapi.pyd")):
                    env = QProcessEnvironment.systemEnvironment()
                    _old = env.value("PYTHONPATH", "")
                    _new = f"{_p};{_old}" if _old else str(_p)
                    env.insert("PYTHONPATH", _new)
                    self.process_service._process.setProcessEnvironment(env)
                    break
        python_exe = _resolve_cm_python(cm_install) if cm_install else None
        self.process_service.start_python(script_path, arguments[1:], calibration_root,
                                          python_executable=python_exe)
