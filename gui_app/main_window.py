from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QWidget

from gui_app.models.state import AppStatus, ApplicationState, CalibrationLaunchConfig, CameraResult
from gui_app.services.calibration_service import CalibrationService
from gui_app.services.config_service import ConfigService
from gui_app.services.precheck_service import PrecheckService
from gui_app.services.runtime_service import RuntimeService
from gui_app.widgets.calibration_panel import CalibrationPanel
from gui_app.widgets.output_panel import OutputPanel
from gui_app.widgets.runtime_panel import RuntimePanel


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = project_root.resolve()
        self.state = ApplicationState()
        self.config_service = ConfigService(self.project_root)
        self.precheck_service = PrecheckService(self.project_root)
        self.runtime_service = RuntimeService(self.project_root, self)
        self.calibration_service = CalibrationService(self.project_root, self)

        self.setWindowTitle("Camera Calibration Console")
        self.resize(1500, 900)

        self.runtime_panel = RuntimePanel(self.project_root, self)
        self.calibration_panel = CalibrationPanel(self)
        self.output_panel = OutputPanel(self)

        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.addWidget(self.runtime_panel, 1)
        layout.addWidget(self.calibration_panel, 1)
        layout.addWidget(self.output_panel, 2)
        self.setCentralWidget(container)

        self._wire_signals()
        self._refresh_camera_list()
        self._apply_status(AppStatus.IDLE)

    def _wire_signals(self) -> None:
        self.calibration_panel.start_button.clicked.connect(self._start_calibration)
        self.calibration_panel.stop_button.clicked.connect(self._stop_calibration)
        self.calibration_panel.precheck_button.clicked.connect(self._run_precheck)
        self.runtime_panel.probe_button.clicked.connect(self._probe_runtime)
        self.runtime_panel.prepare_button.clicked.connect(self._prepare_runtime)

        self.runtime_service.line_received.connect(self.output_panel.append_log)
        self.runtime_service.runtime_summary.connect(self._on_runtime_summary)
        self.runtime_service.process_failed.connect(self._on_runtime_process_failed)
        self.calibration_service.line_received.connect(self.output_panel.append_log)
        self.calibration_service.process_started.connect(self._on_process_started)
        self.calibration_service.process_finished.connect(self._on_process_finished)
        self.calibration_service.process_failed.connect(self._on_process_failed)
        self.calibration_service.orchestration_event.connect(self._on_orchestration_event)
        self.calibration_service.orchestration_summary.connect(self._on_orchestration_summary)

    def _refresh_camera_list(self) -> None:
        self.calibration_panel.set_cameras(self.config_service.list_cameras())

    def _build_launch_config(self) -> CalibrationLaunchConfig:
        selected_cameras = self.calibration_panel.selected_cameras()
        if not selected_cameras:
            raise ValueError("Please select at least one camera")
        testrun = self.runtime_panel.testrun_edit.text().strip()
        if not testrun:
            raise ValueError("TestRun is required")

        self.state.selected_cameras = selected_cameras
        project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
        return CalibrationLaunchConfig(
            project_root=project_root,
            testrun=testrun,
            cameras=selected_cameras,
            campaign_rounds=self.calibration_panel.campaign_rounds_spin.value(),
            multi_start_count=self.calibration_panel.multi_start_count_spin.value(),
            multi_start_iters=self._spin_value_or_none(self.calibration_panel.multi_start_iters_spin),
            multi_start_jitter_steps=self.calibration_panel.jitter_spin.value(),
            refine_iters=self._spin_value_or_none(self.calibration_panel.refine_iters_spin),
            explore_then_refine=self.calibration_panel.explore_then_refine_check.isChecked(),
            resume_from_result=self.calibration_panel.resume_from_result_check.isChecked(),
        )

    @staticmethod
    def _spin_value_or_none(widget) -> int | None:
        value = int(widget.value())
        return None if value <= 0 else value

    @Slot()
    def _start_calibration(self) -> None:
        try:
            launch = self._build_launch_config()
            self.output_panel.log_view.clear()
            self.calibration_service.start(launch)
        except Exception as exc:
            QMessageBox.critical(self, "Start Failed", str(exc))

    @Slot()
    def _stop_calibration(self) -> None:
        self.calibration_service.stop()

    @Slot()
    def _probe_runtime(self) -> None:
        try:
            project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
            testrun = self.runtime_panel.testrun_edit.text().strip()
            if not testrun:
                raise ValueError("TestRun is required")
            self.runtime_service.probe_status(project_root, testrun)
        except Exception as exc:
            QMessageBox.critical(self, "Runtime Probe Failed", str(exc))

    @Slot()
    def _prepare_runtime(self) -> None:
        try:
            project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
            testrun = self.runtime_panel.testrun_edit.text().strip()
            if not testrun:
                raise ValueError("TestRun is required")
            self.runtime_service.prepare_runtime(project_root, testrun)
        except Exception as exc:
            QMessageBox.critical(self, "Prepare Failed", str(exc))

    @Slot()
    def _run_precheck(self) -> None:
        try:
            selected_cameras = self.calibration_panel.selected_cameras()
            if not selected_cameras:
                raise ValueError("Please select at least one camera")
            project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
            if project_root.resolve() != self.precheck_service.project_root:
                self.precheck_service = PrecheckService(project_root)
            results = self.precheck_service.run_for_cameras(selected_cameras)
            self.calibration_panel.update_precheck_results(results)
        except Exception as exc:
            QMessageBox.critical(self, "Precheck Failed", str(exc))

    def _apply_status(self, status: AppStatus) -> None:
        self.state.status = status
        self.runtime_panel.status_label.setText(status.value)
        running = status == AppStatus.RUNNING
        self.calibration_panel.start_button.setEnabled(not running)
        self.calibration_panel.stop_button.setEnabled(running)

    @Slot()
    def _on_process_started(self) -> None:
        self._apply_status(AppStatus.RUNNING)

    @Slot(int)
    def _on_process_finished(self, exit_code: int) -> None:
        if self.state.status == AppStatus.STOPPED:
            return
        self._apply_status(AppStatus.FINISHED if exit_code == 0 else AppStatus.FAILED)

    @Slot(str)
    def _on_process_failed(self, error_text: str) -> None:
        self._apply_status(AppStatus.FAILED)
        QMessageBox.critical(self, "Process Error", error_text)

    @Slot(str)
    def _on_runtime_process_failed(self, error_text: str) -> None:
        QMessageBox.critical(self, "Runtime Process Error", error_text)

    @Slot(dict)
    def _on_runtime_summary(self, payload: dict) -> None:
        self.runtime_panel.set_runtime_summary(payload)
        self.output_panel.append_log(f"[runtime] summary mode={payload.get('mode')} status={payload.get('status', payload.get('mode'))}")

    @Slot(dict)
    def _on_orchestration_event(self, payload: dict) -> None:
        event_name = str(payload.get("event") or "")
        if event_name == "task_started":
            output_dir = str(payload.get("output_dir") or "")
            self.state.output_dir = Path(output_dir) if output_dir else None
            self.runtime_panel.output_dir_label.setText(output_dir or "-")
            self.output_panel.set_output_dir(output_dir or None)
            for camera_name in self.state.selected_cameras:
                self.output_panel.update_camera_result(CameraResult(camera=camera_name))
        elif event_name == "camera_prepare_started":
            camera_name = str(payload.get("camera") or "")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="preparing"))
        elif event_name == "camera_prepare_finished":
            camera_name = str(payload.get("camera") or "")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="ready"))
        elif event_name == "camera_run_started":
            camera_name = str(payload.get("camera") or "")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="running"))
        elif event_name == "camera_run_progress":
            camera_name = str(payload.get("camera") or "")
            progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
            self.output_panel.update_camera_result(
                CameraResult(
                    camera=camera_name,
                    status="running",
                    best_score=self._as_float(progress.get("best_score")),
                    current_iter_score=self._as_float(progress.get("current_iter_score")),
                    current_iter_index=self._as_int(progress.get("current_iter_index")),
                    result_json=self._as_text(progress.get("result_json")),
                    best_image=self._as_text(progress.get("best_image")),
                    best_score_image=self._as_text(progress.get("best_score_image")),
                    best_overlay_image=self._as_text(progress.get("best_overlay_image")),
                )
            )
        elif event_name == "camera_run_finished":
            camera_name = str(payload.get("camera") or "")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="finished"))
        elif event_name == "task_failed":
            self._apply_status(AppStatus.FAILED)
        elif event_name == "task_stopped":
            self._apply_status(AppStatus.STOPPED)

    @Slot(dict)
    def _on_orchestration_summary(self, payload: dict) -> None:
        status = str(payload.get("status") or "")
        if status == "stopped":
            self._apply_status(AppStatus.STOPPED)
        elif status == "failed":
            self._apply_status(AppStatus.FAILED)
        else:
            self._apply_status(AppStatus.FINISHED)

        for entry in payload.get("per_camera", []):
            if not isinstance(entry, dict):
                continue
            camera_name = str(entry.get("camera") or "")
            calibration = entry.get("calibration") if isinstance(entry.get("calibration"), dict) else {}
            result = CameraResult(
                camera=camera_name,
                status=str(entry.get("status") or status or "finished"),
                best_score=self._as_float(calibration.get("best_score")),
                current_iter_score=self._as_float(calibration.get("current_iter_score")),
                current_iter_index=self._as_int(calibration.get("current_iter_index")),
                result_json=self._as_text(calibration.get("result_json")),
                best_image=self._as_text(calibration.get("best_image")),
                best_score_image=self._as_text(calibration.get("best_score_image")),
                best_overlay_image=self._as_text(calibration.get("best_overlay_image")),
            )
            self.output_panel.update_camera_result(result)

    @staticmethod
    def _as_float(value) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_int(value) -> int | None:
        try:
            return None if value is None else int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_text(value) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None