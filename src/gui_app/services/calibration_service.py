from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QProcessEnvironment, Signal

from src.entry.portable_runtime import resolve_tool_root, build_cmapi_pythonpath
from src.gui_app.models.state import CalibrationLaunchConfig
from src.gui_app.services.process_service import ProcessService


class CalibrationService(QObject):
    line_received = Signal(str)
    orchestration_event = Signal(dict)
    orchestration_summary = Signal(dict)
    process_started = Signal()
    process_finished = Signal(int)
    process_failed = Signal(str)

    def __init__(self, project_root: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.calibration_root = resolve_tool_root()
        self.process_service = ProcessService(self)
        self.process_service.line_received.connect(self.line_received.emit)
        self.process_service.orchestration_event.connect(self.orchestration_event.emit)
        self.process_service.orchestration_summary.connect(self.orchestration_summary.emit)
        self.process_service.process_started.connect(self.process_started.emit)
        self.process_service.process_finished.connect(self.process_finished.emit)
        self.process_service.process_failed.connect(self.process_failed.emit)

    @property
    def is_running(self) -> bool:
        return self.process_service.is_running

    def set_cm_install(self, cm_install: Path | None) -> None:
        self._cm_install = cm_install

    @staticmethod
    def _resolve_calibration_root(project_root: Path) -> Path:
        return resolve_tool_root()

    def start(self, launch: CalibrationLaunchConfig) -> None:
        if self.is_running:
            self.stop()
        calibration_root = self._resolve_calibration_root(launch.project_root)
        script_path = calibration_root / "src" / "orchestration" / "calibration_orchestrator.py"
        arguments = [
            "--project-root",
            str(launch.project_root),
            "--testrun",
            launch.testrun,
            "--campaign-rounds",
            str(launch.campaign_rounds),
            "--explore-then-refine",
        ]
        arguments.extend(["--explore-start-count", str(launch.explore_start_count)])
        arguments.extend(["--explore-iters", str(launch.explore_iters)])
        arguments.extend(["--refine-iters", str(launch.refine_iters)])
        if launch.resume_from_result:
            arguments.append("--resume-from-result")
        if launch.skip_prepare_for_first_camera:
            arguments.append("--skip-prepare-for-first-camera")
        if launch.output_dir is not None:
            arguments.extend(["--output-dir", str(launch.output_dir)])
        for camera_name in launch.cameras:
            arguments.extend(["--camera", camera_name])
        cm_install = getattr(self, "_cm_install", None)
        env = QProcessEnvironment.systemEnvironment()
        if cm_install is not None:
            pythonpath, _paths = build_cmapi_pythonpath(
                cm_install,
                existing_pythonpath=env.value("PYTHONPATH", ""),
            )
            if pythonpath:
                env.insert("PYTHONPATH", pythonpath)
        self.process_service._process.setProcessEnvironment(env)
        self.process_service.start_python(script_path, arguments, calibration_root)

    def prepare(self, launch: CalibrationLaunchConfig) -> None:
        if self.is_running:
            return
        calibration_root = self._resolve_calibration_root(launch.project_root)
        script_path = calibration_root / "src" / "orchestration" / "calibration_orchestrator.py"
        arguments = [
            "--project-root", str(launch.project_root),
            "--testrun", launch.testrun,
            "--prepare-only",
        ]
        for camera_name in launch.cameras:
            arguments.extend(["--camera", camera_name])
        cm_install = getattr(self, "_cm_install", None)
        env = QProcessEnvironment.systemEnvironment()
        if cm_install is not None:
            pythonpath, _paths = build_cmapi_pythonpath(
                cm_install,
                existing_pythonpath=env.value("PYTHONPATH", ""),
            )
            if pythonpath:
                env.insert("PYTHONPATH", pythonpath)
        self.process_service._process.setProcessEnvironment(env)
        self.process_service.start_python(script_path, arguments, calibration_root)
    def stop(self) -> None:
        self.process_service.stop()
