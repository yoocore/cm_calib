from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcessEnvironment, Signal

from gui_app.services.process_service import ProcessService


def resolve_cmapi_path(cm_install: Path) -> Path | None:
    candidates = [
        cm_install / "Python" / "Lib" / "site-packages",
        cm_install / "Python" / "Lib",
        cm_install.parent / "Python" / "Lib" / "site-packages",
        cm_install / "pylib",
    ]
    for p in candidates:
        if (p / "cmapi").is_dir():
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
            cmapi_path = resolve_cmapi_path(cm_install)
            if cmapi_path is not None:
                env = QProcessEnvironment.systemEnvironment()
                old_pypath = env.value("PYTHONPATH", "")
                new_pypath = f"{cmapi_path};{old_pypath}" if old_pypath else str(cmapi_path)
                env.insert("PYTHONPATH", new_pypath)
                self.process_service._process.setProcessEnvironment(env)
        self.process_service.start_python(script_path, arguments[1:], calibration_root)
