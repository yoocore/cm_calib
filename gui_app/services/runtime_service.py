from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from gui_app.services.process_service import ProcessService


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

    def probe_status(self, project_root: Path, testrun: str) -> None:
        self._start_mode("status", project_root, testrun)

    def prepare_runtime(self, project_root: Path, testrun: str) -> None:
        self._start_mode("prepare", project_root, testrun)

    def stop(self) -> None:
        self.process_service.stop()

    def _start_mode(self, mode: str, project_root: Path, testrun: str) -> None:
        script_path = self.calibration_root / "cmapi_testrun_control.py"
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
        self.process_service.start_python(script_path, arguments[1:], self.calibration_root)
