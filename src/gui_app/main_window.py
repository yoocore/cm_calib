from __future__ import annotations

import ctypes
import json
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QCoreApplication, QTimer, Slot
from PySide6.QtWidgets import QMainWindow, QMessageBox, QSplitter, QVBoxLayout, QWidget

from src.gui_app.models.state import AppStatus, ApplicationState, CalibrationLaunchConfig, CameraResult
from src.gui_app.services.calibration_service import CalibrationService
from src.gui_app.services.config_service import ConfigService
from src.gui_app.services.precheck_service import PrecheckService
from src.gui_app.services.runtime_service import RuntimeService
from src.gui_app.services.static_vehicle_reader import resolve_vehicle_info

from src.gui_app.widgets.calibration_panel import CalibrationPanel
from src.gui_app.widgets.cm_settings_panel import CmSettingsPanel
from src.gui_app.widgets.output_panel import OutputPanel
from src.gui_app.widgets.sensor_progress_panel import SensorProgressPanel


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = project_root.resolve()
        self._last_runtime_summary: dict | None = None
        self._runtime_recent_lines: deque[str] = deque(maxlen=12)
        self._status_summary_lines: deque[str] = deque(maxlen=10)
        self._calibration_recent_lines: deque[str] = deque(maxlen=20)
        self._calibration_task_started_at: float | None = None
        self._camera_started_at: dict[str, float] = {}
        self._camera_elapsed_final: dict[str, float] = {}
        self._camera_progress_status: dict[str, str] = {}
        self._camera_progress_detail: dict[str, str] = {}
        self._camera_progress_iter_text: dict[str, str] = {}
        self._camera_progress_current_score: dict[str, str] = {}
        self._camera_progress_best_score: dict[str, str] = {}
        self._camera_progress_init_score: dict[str, float] = {}
        self._camera_progress_current_iter: dict[str, int] = {}
        self._camera_progress_total_iters: dict[str, int] = {}
        self._camera_task_best_progress: dict[str, dict[str, object]] = {}
        self._camera_last_progress: dict[str, dict[str, object]] = {}
        self._camera_last_phase: dict[str, str] = {}
        self._camera_progress_accrued_base: dict[str, int] = {}
        self.state = ApplicationState()
        self.config_service = ConfigService(self.project_root)
        self.precheck_service = PrecheckService(self.project_root)
        self.runtime_service = RuntimeService(self.project_root, self)
        self.calibration_service = CalibrationService(self.project_root, self)

        self.setWindowTitle("Camera Calibration Console")
        self.resize(1500, 900)

        self.cm_settings_panel = CmSettingsPanel(self)
        self.calibration_panel = CalibrationPanel(self)
        self.sensor_progress_panel = SensorProgressPanel(self)
        self.output_panel = OutputPanel(self)

        self.left_mid_splitter = QSplitter(Qt.Horizontal, self)
        self.left_mid_splitter.addWidget(self.cm_settings_panel)
        self.left_mid_splitter.addWidget(self.calibration_panel)
        self.left_mid_splitter.setSizes([400, 300])
        self.left_mid_splitter.setHandleWidth(8)
        self.left_mid_splitter.setCollapsible(0, False)
        self.left_mid_splitter.setCollapsible(1, False)
        self.left_mid_splitter.setStretchFactor(0, 0)
        self.left_mid_splitter.setStretchFactor(1, 0)

        left_mid_container = QWidget(self)
        left_mid_container.setMinimumWidth(700)
        left_mid_layout = QVBoxLayout(left_mid_container)
        left_mid_layout.setContentsMargins(0, 0, 0, 0)
        left_mid_layout.setSpacing(10)
        left_mid_layout.addWidget(self.left_mid_splitter, 4)
        left_mid_layout.addWidget(self.sensor_progress_panel, 3)

        outer_splitter = QSplitter(Qt.Horizontal, self)
        outer_splitter.addWidget(left_mid_container)
        outer_splitter.addWidget(self.output_panel)
        outer_splitter.setSizes([700, 800])
        outer_splitter.setHandleWidth(8)
        outer_splitter.setCollapsible(0, False)
        outer_splitter.setCollapsible(1, False)
        outer_splitter.setStretchFactor(0, 0)
        outer_splitter.setStretchFactor(1, 0)
        self.output_panel.setMinimumWidth(400)

        central_container = QWidget(self)
        central_layout = QVBoxLayout(central_container)
        central_layout.setContentsMargins(10, 10, 10, 10)
        central_layout.addWidget(outer_splitter)
        self.setCentralWidget(central_container)

        QTimer.singleShot(0, lambda: self.left_mid_splitter.setSizes([400, 300]))

        self._refresh_static_timer = QTimer(self)
        self._refresh_static_timer.setInterval(1000)
        self._refresh_static_timer.timeout.connect(self._refresh_static_info)
        self._refresh_static_timer.start()
        self._wire_signals()
        self._refresh_camera_list()
        self._apply_status(AppStatus.IDLE)
        self.calibration_panel.clear_failure_summary()

    def _wire_signals(self) -> None:
        self.calibration_panel.start_button.clicked.connect(self._start_calibration)
        self.calibration_panel.stop_button.clicked.connect(self._stop_calibration)
        self.calibration_panel.prepare_button.clicked.connect(self._cm_prepare)
        self.cm_settings_panel.wizard_for_camera_clicked.connect(self._open_board_wizard_for_camera)
        self.cm_settings_panel.project_root_changed.connect(self._on_project_root_changed)
        self.cm_settings_panel.testrun_changed.connect(self._on_testrun_changed)
        self.cm_settings_panel.camera_selection_changed.connect(self._rebuild_sensor_progress_plan)
        self.calibration_panel.estimated_time_changed.connect(self._rebuild_sensor_progress_plan)

        self.runtime_service.line_received.connect(self._on_runtime_line)
        self.runtime_service.runtime_summary.connect(self._on_runtime_summary)
        self.runtime_service.process_started.connect(self._on_runtime_process_started)
        self.runtime_service.process_finished.connect(self._on_runtime_process_finished)
        self.runtime_service.process_failed.connect(self._on_runtime_process_failed)
        self.calibration_service.line_received.connect(self._on_calibration_line)
        self.calibration_service.process_started.connect(self._on_process_started)
        self.calibration_service.process_finished.connect(self._on_process_finished)
        self.calibration_service.process_failed.connect(self._on_process_failed)
        self.calibration_service.orchestration_event.connect(self._on_orchestration_event)
        self.calibration_service.orchestration_summary.connect(self._on_orchestration_summary)

    def _refresh_camera_list(self) -> None:
        project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
        testrun = self.cm_settings_panel.testrun_edit.text().strip()
        if not testrun:
            self.cm_settings_panel.set_cameras([])
            return

        # Always read sensor list from vehicle file — it's the source of truth
        try:
            from src.gui_app.services.static_vehicle_reader import resolve_vehicle_info
            info = resolve_vehicle_info(project_root, testrun)
            sensors = [s["name"] for s in info.get("sensors", [])]
        except Exception as exc:
            sensors = []
            self.output_panel.append_log(f"Vehicle sensor read failed: {exc}", source="system")

        # Check mapping to log which have configs
        from src.gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
        mapping_path = mapping_path_for_project(str(project_root))
        if mapping_path.exists():
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            mapped_names = [n for n in sensors if n in mapping]
            if mapped_names:
                self.output_panel.append_log(
                    f"Camera sensors: {', '.join(sensors)} ({len(mapped_names)} mapped)", source="system",
                )
            else:
                self.output_panel.append_log(
                    f"Camera sensors: {', '.join(sensors)} — run Wizard to map each", source="system",
                )
        else:
            self.output_panel.append_log(
                f"Camera sensors: {', '.join(sensors)} — no mapping file yet", source="system",
            )

        self.cm_settings_panel.set_cameras(sensors)

    def _refresh_static_info(self) -> None:
        project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
        testrun = self.cm_settings_panel.testrun_edit.text().strip()
        if not testrun:
            self.cm_settings_panel.vehicle_label.setText("-")
            self.cm_settings_panel.clear_sensor_list()
            self._refresh_calibration_progress()
            return
        try:
            info = resolve_vehicle_info(project_root, testrun)
            self.cm_settings_panel.vehicle_label.setText(info["vehicle_key"])
            self.cm_settings_panel.update_sensor_list(info["sensors"])
        except Exception:
            self.cm_settings_panel.vehicle_label.setText("-")
            self.cm_settings_panel.clear_sensor_list()
        self._refresh_calibration_progress()

    def _set_red_failure(self, text: str) -> None:
        self.calibration_panel.set_failure_summary(text)

    def _restore_gui_to_foreground(self) -> None:
        self.raise_()
        self.activateWindow()
        try:
            user32 = ctypes.windll.user32
        except Exception:
            return

        hwnd = int(self.winId())
        if hwnd <= 0:
            return

        swp_flags = 0x0001 | 0x0002 | 0x0040
        try:
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, swp_flags)
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, swp_flags)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            return

    def _set_status_summary(self, text: str | None) -> None:
        self._status_summary_lines.clear()
        lines = [str(raw).strip() for raw in str(text or "").splitlines()]
        for line in lines:
            if not line:
                continue
            if self._status_summary_lines and self._status_summary_lines[-1] == line:
                continue
            self._status_summary_lines.append(line)
        self.calibration_panel.set_failure_summary("\n".join(self._status_summary_lines))

    def _append_status_summary_line(self, text: str | None) -> None:
        line = self._as_text(text)
        if not line:
            return
        if self._status_summary_lines and self._status_summary_lines[-1] == line:
            return
        self._status_summary_lines.append(line)
        self.calibration_panel.set_failure_summary("\n".join(self._status_summary_lines))

    def _progress_cameras(self) -> list[str]:
        return list(self.state.selected_cameras or self.cm_settings_panel.selected_cameras())

    def _reset_calibration_progress_tracking(self) -> None:
        cameras = self._progress_cameras()
        self._calibration_task_started_at = None
        self._camera_started_at = {}
        self._camera_elapsed_final = {}
        self._camera_progress_status = {camera_name: "pending" for camera_name in cameras}
        self._camera_progress_detail = {}
        self._camera_progress_iter_text = {}
        self._camera_progress_current_score = {}
        self._camera_progress_best_score = {}
        self._camera_progress_init_score = {}
        self._camera_progress_current_iter = {}
        self._camera_progress_total_iters = {}
        self._camera_task_best_progress = {}
        self._camera_last_progress = {}
        self._camera_last_phase = {}
        self._camera_progress_accrued_base = {}
        self._rebuild_sensor_progress_plan()
        self._refresh_calibration_progress()

    def _begin_calibration_progress_tracking(self) -> None:
        self._reset_calibration_progress_tracking()
        self._calibration_task_started_at = time.monotonic()
        self._refresh_calibration_progress()

    def _rebuild_sensor_progress_plan(self) -> None:
        cameras = self.cm_settings_panel.selected_cameras()
        self.sensor_progress_panel.reset_sensor_progress(cameras)

    def _set_camera_progress_state(
        self,
        camera_name: str | None,
        status: str,
        *,
        detail: str | None = None,
        finalize: bool = False,
    ) -> None:
        camera_key = self._as_text(camera_name)
        if not camera_key:
            return
        if status in {"preparing", "ready", "running"} and camera_key not in self._camera_started_at:
            self._camera_started_at[camera_key] = time.monotonic()
        self._camera_progress_status[camera_key] = status
        if detail:
            self._camera_progress_detail[camera_key] = detail
        elif camera_key in self._camera_progress_detail:
            self._camera_progress_detail.pop(camera_key, None)
        if finalize and camera_key not in self._camera_elapsed_final:
            started_at = self._camera_started_at.get(camera_key)
            if started_at is not None:
                self._camera_elapsed_final[camera_key] = max(0.0, time.monotonic() - started_at)
        self._refresh_calibration_progress()

    def _finalize_active_camera(self, status: str) -> None:
        for camera_name in self._progress_cameras():
            if self._camera_progress_status.get(camera_name) in {"preparing", "ready", "running"}:
                self._set_camera_progress_state(camera_name, status, finalize=True)
                return

    def _refresh_calibration_progress(self) -> None:
        cameras = self._progress_cameras()
        if not cameras:
            self._rebuild_sensor_progress_plan()
            return

        now = time.monotonic()
        total_iter_current = 0
        total_iter_max = 0
        completed_count = 0
        running_camera: str | None = None
        preparing_camera: str | None = None
        ready_camera: str | None = None

        for camera_name in cameras:
            status = self._camera_progress_status.get(camera_name, "pending")
            started_at = self._camera_started_at.get(camera_name)
            elapsed = self._camera_elapsed_final.get(camera_name)
            if elapsed is None and started_at is not None and status in {"preparing", "ready", "running", "failed", "stopped"}:
                elapsed = max(0.0, now - started_at)
            elapsed_seconds = int(round(elapsed or 0.0))
            detail = self._camera_progress_detail.get(camera_name)

            current_iter = self._camera_progress_current_iter.get(camera_name, 0) or 0
            total_iters = self._camera_progress_total_iters.get(camera_name, 0) or 0

            if status == "finished":
                progress_percent = 100
                completed_count += 1
                total_iter_current += total_iters if total_iters > 0 else 0
                total_iter_max += total_iters if total_iters > 0 else 0
            elif status in {"preparing", "ready", "running"}:
                if total_iters > 0:
                    progress_percent = int(min(99, max(0, round((current_iter / total_iters) * 100))))
                else:
                    progress_percent = 0
                total_iter_current += current_iter
                total_iter_max += total_iters
                if status == "running" and running_camera is None:
                    running_camera = camera_name
                elif status == "preparing" and preparing_camera is None:
                    preparing_camera = camera_name
                elif status == "ready" and ready_camera is None:
                    ready_camera = camera_name
            elif status in {"failed", "stopped"}:
                if total_iters > 0:
                    progress_percent = int(min(99, max(0, round((current_iter / total_iters) * 100))))
                else:
                    progress_percent = 0
                total_iter_current += current_iter
                total_iter_max += total_iters
            else:
                progress_percent = 0

            self.sensor_progress_panel.set_sensor_progress(
                camera_name,
                status=status,
                progress_percent=progress_percent,
                elapsed_seconds=elapsed_seconds,
                detail=detail,
                iter_text=self._camera_progress_iter_text.get(camera_name),
                init_score_text=f"{self._camera_progress_init_score[camera_name]:.2f}" if camera_name in self._camera_progress_init_score else None,
                current_score_text=self._camera_progress_current_score.get(camera_name),
                best_score_text=self._camera_progress_best_score.get(camera_name),
            )

        if self._calibration_task_started_at is not None:
            elapsed_total_seconds = int(round(max(0.0, now - self._calibration_task_started_at)))
        else:
            elapsed_total_seconds = int(round(sum(self._camera_elapsed_final.values())))
        overall_percent = int(round((total_iter_current / total_iter_max) * 100)) if total_iter_max > 0 else 0
        if any(self._camera_progress_status.get(c) not in {"finished"} for c in cameras):
            overall_percent = min(99, overall_percent)
        self.sensor_progress_panel.set_overall_progress(
            current_camera=running_camera or preparing_camera or ready_camera,
            completed_count=completed_count,
            total_count=len(cameras),
            progress_percent=min(100, max(0, overall_percent)),
            elapsed_seconds=elapsed_total_seconds,
        )

    def _on_project_root_changed(self, path_text: str) -> None:
        new_root = Path(path_text.strip()).resolve() if path_text.strip() else None
        if new_root and new_root != self.config_service.project_root:
            self.output_panel.append_log(f"Project root: {new_root}", source="system")
            self.project_root = new_root
            self.config_service = ConfigService(new_root)
            self.precheck_service = PrecheckService(new_root)
        self._refresh_camera_list()

    def _on_testrun_changed(self, _text: str) -> None:
        self.output_panel.append_log(f"TestRun selected", source="system")
        self._refresh_camera_list()

    def _build_launch_config(self) -> CalibrationLaunchConfig:
        selected_cameras = self.cm_settings_panel.selected_cameras()
        if not selected_cameras:
            raise ValueError("Please select at least one camera")
        testrun = self.cm_settings_panel.testrun_edit.text().strip()
        if not testrun:
            raise ValueError("TestRun is required")

        self.state.selected_cameras = selected_cameras
        project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
        ref_iters = int(self.calibration_panel._er_refine_iters_spin.value())
        explore_iters = int(self.calibration_panel._er_iters_spin.value())
        explore_start_count = self.calibration_panel._er_count_spin.value()
        return CalibrationLaunchConfig(
            project_root=project_root,
            testrun=testrun,
            cameras=selected_cameras,
            campaign_rounds=self.calibration_panel.campaign_rounds_spin.value(),
            refine_iters=ref_iters,
            explore_then_refine=True,
            explore_start_count=explore_start_count,
            explore_iters=explore_iters,
            skip_prepare_for_first_camera=True,
        )


    @Slot()
    def _start_calibration(self) -> None:
        self.calibration_panel.start_button.setEnabled(False)
        QCoreApplication.processEvents()
        try:
            launch = self._build_launch_config()
        except Exception as exc:
            self.calibration_panel.set_failure_summary(str(exc))
            QMessageBox.critical(self, "Start Failed", str(exc))
            self._sync_control_states()
            return

        try:
            project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
            if project_root.resolve() != self.precheck_service.project_root:
                self.precheck_service = PrecheckService(project_root)
            precheck_results = self.precheck_service.run_for_cameras(launch.cameras)
            self.cm_settings_panel.update_precheck_results(precheck_results)
            # Log precheck results to output panel
            for _r in precheck_results:
                _icon = "✓" if _r.get("ok") else "✗"
                self.output_panel.append_log(
                    f"Precheck {_icon} {_r.get('camera', '?')}: {_r.get('message', '')}",
                    source="system",
                )
            failed = [r for r in precheck_results if not r.get("ok")]
            if failed:
                messages = [str(r.get("message", "")) for r in failed]
                self.calibration_panel.set_failure_summary("Precheck failed: " + "; ".join(messages))
                QMessageBox.critical(self, "Precheck Failed", "Precheck failed. See the Precheck tree and failure summary for details.")
                self._sync_control_states()
                return
        except Exception as exc:
            self.calibration_panel.set_failure_summary("Precheck error: " + str(exc))
            QMessageBox.critical(self, "Precheck Error", str(exc))
            self._sync_control_states()
            return

        try:
            cm_install = self.calibration_panel.cm_install_path
            if cm_install is None:
                raise ValueError("CM version is not selected. Choose a CM version first.")
            self.calibration_service.set_cm_install(cm_install)
            if self.calibration_service.is_running:
                self.output_panel.append_log(
                    f"calibration_service already running; stopping first",
                    source="system",
                )
                self.calibration_service.stop()
            self.output_panel.append_log("─" * 60, source="system")
            self._set_status_summary("Calib Start triggered. Starting calibration...")
            self.calibration_service.start(launch)
        except Exception as exc:
            self._set_status_summary(str(exc))
            QMessageBox.critical(self, "Start Failed", str(exc))
            self._sync_control_states()

    @Slot()
    def _stop_calibration(self) -> None:
        if self.state.status == AppStatus.RUNNING:
            self.calibration_service.stop()

    def _open_board_wizard_for_camera(self, camera_name: str) -> None:
        from src.gui_app.widgets.bootstrap_wizard import BootstrapWizardDialog
        project_dir = self.cm_settings_panel.project_root_edit.text().strip() or None
        testrun = self.cm_settings_panel.testrun_edit.text().strip() or None
        dialog = BootstrapWizardDialog(
            self,
            project_dir=project_dir,
            testrun=testrun,
            camera_name=camera_name,
        )
        dialog.exec()
        self._refresh_camera_list()  # Refresh to pick up mapping changes from wizard
    def _cm_prepare(self) -> None:
        """Kill existing CM, restart, prepare environment."""
        try:
            launch = self._build_launch_config()
        except Exception as exc:
            self._set_status_summary(str(exc))
            QMessageBox.critical(self, "CM Prepare Failed", str(exc))
            return

        try:
            cm_install = self.calibration_panel.cm_install_path
            if cm_install is None:
                raise ValueError("CM version is not selected. Choose a CM version first.")
            self.calibration_service.set_cm_install(cm_install)

            if self.calibration_service.is_running:
                self.output_panel.append_log(
                    "calibration_service already running; stopping first",
                    source="system",
                )
                self.calibration_service.stop()

            self._apply_status(AppStatus.PREPARING)
            self._set_status_summary("CM Prepare: restarting environment...")
            self.output_panel.append_log("─" * 60, source="system")
            self.calibration_service.prepare(launch)
        except Exception as exc:
            self._set_status_summary(str(exc))
            QMessageBox.critical(self, "CM Prepare Failed", str(exc))
            self._sync_control_states()

    def _apply_status(self, status: AppStatus) -> None:
        self.state.status = status
        self.calibration_panel.set_status(status.value)
        if status in {AppStatus.FINISHED, AppStatus.FAILED, AppStatus.STOPPED}:
            self._calibration_task_started_at = None
            self._refresh_calibration_progress()
        self._sync_control_states()

    def _sync_control_states(self) -> None:
        calibration_running = self.state.status == AppStatus.RUNNING
        can_start = self.state.status in {AppStatus.IDLE, AppStatus.READY, AppStatus.FINISHED, AppStatus.FAILED, AppStatus.STOPPED, AppStatus.PASSIVE}
        running_or_locked = calibration_running or self.calibration_service.is_running
        self.calibration_panel.start_button.setEnabled(can_start and not running_or_locked)
        self.calibration_panel.prepare_button.setEnabled(can_start and not running_or_locked)
        self.calibration_panel.stop_button.setEnabled(calibration_running)
        controls_enabled = not running_or_locked
        self.cm_settings_panel.set_inputs_locked(not controls_enabled)
        self.calibration_panel.set_inputs_locked(not controls_enabled)

    @Slot()
    def _on_process_started(self) -> None:
        self._calibration_recent_lines.clear()
        self._begin_calibration_progress_tracking()
        self._set_status_summary("Calibration in progress...\nWaiting for orchestration events and per-camera results.")
        self.calibration_panel.set_phase_label("Calibration in progress...")
        self._apply_status(AppStatus.RUNNING)

    @Slot(int)
    def _on_process_finished(self, exit_code: int) -> None:
        if self.state.status == AppStatus.STOPPED:
            return
        # Clear calibration snapshot so progress reflects current UI selection
        self.state.selected_cameras = []
        if exit_code != 0:
            self._finalize_active_camera("failed")
            self.calibration_panel.set_failure_summary(self._build_failure_summary("Calibration failed", self._calibration_recent_lines))
        self.calibration_panel.set_phase_label("")
        self._apply_status(AppStatus.FINISHED if exit_code == 0 else AppStatus.FAILED)
        self._refresh_calibration_progress()

    @Slot(str)
    def _on_process_failed(self, error_text: str) -> None:
        self._finalize_active_camera("failed")
        self.calibration_panel.set_failure_summary(self._build_failure_summary("Calibration process error", [error_text, *self._calibration_recent_lines]))
        self.calibration_panel.set_phase_label("")
        self._apply_status(AppStatus.FAILED)
        self._refresh_calibration_progress()
        QMessageBox.critical(self, "Process Error", error_text)

    @Slot(str)
    def _on_runtime_process_failed(self, error_text: str) -> None:
        self.calibration_panel.set_failure_summary(self._build_failure_summary("Runtime process error", [error_text, *self._runtime_recent_lines]))
        self._sync_control_states()
        QMessageBox.critical(self, "Runtime Process Error", error_text)

    @Slot()
    def _on_runtime_process_started(self) -> None:
        self._runtime_recent_lines.clear()
        self._sync_control_states()

    @Slot(int)
    def _on_runtime_process_finished(self, exit_code: int) -> None:
        if exit_code != 0:
            self.calibration_panel.set_failure_summary(self._build_failure_summary("Runtime process error", self._runtime_recent_lines))
        self._sync_control_states()

    @Slot(dict)
    def _on_runtime_summary(self, payload: dict) -> None:
        self._last_runtime_summary = payload
        summary_parts = [
            f"mode={payload.get('mode')}",
            f"status={payload.get('status', payload.get('mode'))}",
        ]
        testrun_control = self._as_text(payload.get("testrun_control"))
        if testrun_control:
            summary_parts.append(f"testrun_control={testrun_control}")
        self.output_panel.append_log(f"summary {' '.join(summary_parts)}", source="runtime")
        self._sync_control_states()

    @Slot(dict)
    def _on_orchestration_event(self, payload: dict) -> None:
        event_name = str(payload.get("event") or "")
        if event_name == "task_started":
            if self._calibration_task_started_at is None:
                self._begin_calibration_progress_tracking()
            output_dir = str(payload.get("output_dir") or "")
            self.state.output_dir = Path(output_dir) if output_dir else None
            self.output_panel.set_output_dir(output_dir or None)
            self.output_panel.set_log_path(str(Path(output_dir) / "events.jsonl") if output_dir else None)
            self._append_status_summary_line(
                "Calibration task started."
                + (f" output_dir={output_dir}" if output_dir else "")
            )
            for camera_name in self.state.selected_cameras:
                self.output_panel.update_camera_result(CameraResult(camera=camera_name))
        elif event_name == "camera_prepare_started":
            camera_name = str(payload.get("camera") or "")
            self.output_panel.append_log(f"{camera_name}: runtime prepare starting...", source="calibration")
            self._set_camera_progress_state(camera_name, "preparing")
            self._append_status_summary_line(f"{camera_name}: runtime prepare started.")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="preparing"))
        elif event_name == "camera_prepare_finished":
            camera_name = str(payload.get("camera") or "")
            reused = bool(payload.get("reused_existing_runtime"))
            label = "reused existing runtime" if reused else "full prepare done"
            self.output_panel.append_log(f"{camera_name}: runtime ready ({label})", source="calibration")
            self._set_camera_progress_state(camera_name, "ready")
            self._append_status_summary_line(f"{camera_name}: runtime ready.")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="ready"))
        elif event_name == "camera_run_started":
            camera_name = str(payload.get("camera") or "")
            self._set_camera_progress_state(camera_name, "running")
            self._camera_task_best_progress.pop(camera_name, None)
            self._camera_last_progress.pop(camera_name, None)
            self._append_status_summary_line(f"{camera_name}: calibration started.")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="running"))
        elif event_name == "camera_run_progress":
            camera_name = str(payload.get("camera") or "")
            progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
            self._camera_last_progress[camera_name] = dict(progress)
            global_best = self._merge_camera_task_best_progress(camera_name, progress)
            best_score = self._as_float(global_best.get("best_score"))
            iter_index = self._as_int(progress.get("current_iter_index"))
            current_score = self._as_float(progress.get("current_iter_score"))
            calib_phase = str(progress.get("calib_phase") or "")
            calib_dir_index = self._as_int(progress.get("calib_dir_index"))
            calib_total_dirs = self._as_int(progress.get("calib_total_dirs"))
            calib_max_iters = self._as_int(progress.get("calib_max_iters"))
            calib_round_index = self._as_int(progress.get("calib_round_index"))
            calib_round_count = self._as_int(progress.get("calib_round_count"))
            calib_overall_total_iters = self._as_int(progress.get("calib_overall_total_iters"))
            stop_reason = str(progress.get("stop_reason") or "")
            budget = calib_overall_total_iters
            # Compute cumulative iteration across phases/dirs/rounds
            cumulative_iter = iter_index
            if budget and calib_round_count:
                per_round = budget // calib_round_count
                round_offset = (calib_round_index - 1) * per_round
                if calib_phase == "explore":
                    cumulative_iter = round_offset + (calib_dir_index or 0) * (calib_max_iters or 0) + (iter_index or 0)
                elif calib_phase == "refine":
                    explore_part = per_round - (calib_max_iters or 0)
                    cumulative_iter = round_offset + explore_part + (iter_index or 0)
            round_prefix = f"Rd:{calib_round_index} " if calib_round_index and calib_round_index > 0 else ""
            reason_text = f" reason={stop_reason}" if stop_reason and stop_reason not in ("running", "") else ""
            print(f"[PROGRESS_DIAG] {camera_name}: iter={cumulative_iter}/{budget} phase={calib_phase} dir={calib_dir_index}/{calib_total_dirs} round={calib_round_index}/{calib_round_count}{reason_text}")

            last_phase = self._camera_last_phase.get(camera_name)
            if calib_overall_total_iters and calib_round_count:
                pass
            elif last_phase and last_phase != calib_phase:
                prev_total = self._camera_progress_total_iters.get(camera_name, 0)
                base = self._camera_progress_accrued_base.get(camera_name, 0)
                self._camera_progress_accrued_base[camera_name] = base + prev_total
                self._camera_progress_current_iter[camera_name] = 0
                self._camera_progress_total_iters[camera_name] = 0
            self._camera_last_phase[camera_name] = calib_phase

            if calib_phase == "explore":
                dir_label = f"{calib_dir_index + 1}" if calib_dir_index is not None else "?"
                iter_label = f"{iter_index}" if iter_index is not None else ""
                self._camera_progress_iter_text[camera_name] = f"{round_prefix}E:D{dir_label} I{iter_label}"
                if calib_overall_total_iters and calib_round_count:
                    per_round = calib_overall_total_iters // calib_round_count
                    round_offset = (calib_round_index - 1) * per_round
                    current = round_offset + (calib_dir_index or 0) * (calib_max_iters or 0) + (iter_index or 0)
                    self._camera_progress_current_iter[camera_name] = current
                    self._camera_progress_total_iters[camera_name] = calib_overall_total_iters
                elif calib_dir_index is not None and calib_total_dirs and calib_max_iters:
                    accrued = self._camera_progress_accrued_base.get(camera_name, 0)
                    phase_total = int(calib_total_dirs) * int(calib_max_iters)
                    current = int(calib_dir_index) * int(calib_max_iters) + (iter_index or 0)
                    self._camera_progress_current_iter[camera_name] = accrued + current
                    self._camera_progress_total_iters[camera_name] = accrued + phase_total
            elif calib_phase == "refine":
                iter_label = f"{iter_index}" if iter_index is not None else ""
                self._camera_progress_iter_text[camera_name] = f"{round_prefix}R:I{iter_label}"
                if calib_overall_total_iters and calib_round_count:
                    per_round = calib_overall_total_iters // calib_round_count
                    round_offset = (calib_round_index - 1) * per_round
                    explore_part = per_round - (calib_max_iters or 0)
                    current = round_offset + explore_part + (iter_index or 0)
                    self._camera_progress_current_iter[camera_name] = current
                    self._camera_progress_total_iters[camera_name] = calib_overall_total_iters
                elif calib_max_iters:
                    accrued = self._camera_progress_accrued_base.get(camera_name, 0)
                    self._camera_progress_current_iter[camera_name] = accrued + (iter_index or 0)
                    self._camera_progress_total_iters[camera_name] = accrued + int(calib_max_iters)
            if current_score is not None:
                self._camera_progress_current_score[camera_name] = f"{current_score:.2f}"
            if best_score is not None:
                self._camera_progress_best_score[camera_name] = f"{best_score:.2f}"
            start_score = self._as_float(progress.get("start_score"))
            if start_score is not None and camera_name not in self._camera_progress_init_score:
                self._camera_progress_init_score[camera_name] = start_score
            self._set_camera_progress_state(camera_name, "running")
            progress_line = f"{camera_name}: iter={iter_index or '?'}"
            if best_score is not None:
                progress_line += f" best={best_score:.2f}"
            self.output_panel.append_log(progress_line, source="calibration")
            self._append_status_summary_line(progress_line)
            self.output_panel.update_camera_result(
                CameraResult(
                    camera=camera_name,
                    status="running",
                    live_log=self._as_text(progress.get("live_log")),
                    best_score=best_score,
                    init_score=self._camera_progress_init_score.get(camera_name),
                    current_iter_score=self._as_float(progress.get("current_iter_score")),
                    current_iter_index=self._as_int(progress.get("current_iter_index")),
                    current_iter_image=self._as_text(progress.get("current_iter_image")),
                    result_json=self._as_text(progress.get("result_json")),
                    best_image=self._as_text(global_best.get("best_image")),
                    best_score_image=self._as_text(global_best.get("best_score_image")),
                    best_overlay_image=self._as_text(global_best.get("best_overlay_image")),
                )
            )
        elif event_name == "camera_run_finished":
            camera_name = str(payload.get("camera") or "")
            self._set_camera_progress_state(camera_name, "finished", finalize=True)
            self._append_status_summary_line(f"{camera_name}: calibration finished.")
            best_progress = self._camera_task_best_progress.get(camera_name, {})
            last_progress = self._camera_last_progress.get(camera_name, {})
            self.output_panel.update_camera_result(
                CameraResult(
                    camera=camera_name,
                    status="finished",
                    live_log=self._as_text(best_progress.get("live_log")),
                    best_score=self._as_float(best_progress.get("best_score")),
                    init_score=self._camera_progress_init_score.get(camera_name),
                    current_iter_score=self._as_float(last_progress.get("current_iter_score")),
                    current_iter_index=self._as_int(last_progress.get("current_iter_index")),
                    current_iter_image=self._as_text(last_progress.get("current_iter_image")),
                    result_json=self._as_text(best_progress.get("result_json")),
                    best_image=self._as_text(best_progress.get("best_image")),
                    best_score_image=self._as_text(best_progress.get("best_score_image")),
                    best_overlay_image=self._as_text(best_progress.get("best_overlay_image")),
                )
            )
        elif event_name == "task_failed":
            self._finalize_active_camera("failed")
            self._set_status_summary(self._build_failure_summary("Calibration task failed", [self._as_text(payload.get("error")) or "Unknown failure", *self._calibration_recent_lines]))
            self._apply_status(AppStatus.FAILED)
        elif event_name == "task_stopped":
            self._finalize_active_camera("stopped")
            error_text = self._as_text(payload.get("error"))
            if error_text:
                self._set_status_summary(error_text)
            self._apply_status(AppStatus.STOPPED)

    @Slot(dict)
    def _on_orchestration_summary(self, payload: dict) -> None:
        status = str(payload.get("status") or "")
        if status == "stopped":
            self._finalize_active_camera("stopped")
            if payload.get("error"):
                self._set_status_summary(str(payload.get("error")))
            self._apply_status(AppStatus.STOPPED)
        elif status == "failed":
            self._finalize_active_camera("failed")
            self._set_status_summary(self._build_failure_summary("Calibration task failed", [self._as_text(payload.get("error")) or "Unknown failure", *self._calibration_recent_lines]))
            self._apply_status(AppStatus.FAILED)
        else:
            self._append_status_summary_line("Calibration task finished.")
            self._apply_status(AppStatus.FINISHED)

        for entry in payload.get("per_camera", []):
            if not isinstance(entry, dict):
                continue
            camera_name = str(entry.get("camera") or "")
            calibration = entry.get("calibration") if isinstance(entry.get("calibration"), dict) else {}
            last_progress = self._camera_last_progress.get(camera_name, {})
            result = CameraResult(
                camera=camera_name,
                status=str(entry.get("status") or status or "finished"),
                live_log=self._as_text(calibration.get("live_log")) or self._as_text(last_progress.get("live_log")),
                best_score=self._as_float(calibration.get("best_score")),
                init_score=self._camera_progress_init_score.get(camera_name),
                current_iter_score=self._as_float(calibration.get("current_iter_score")),
                current_iter_index=self._as_int(calibration.get("current_iter_index")),
                current_iter_image=self._as_text(calibration.get("current_iter_image")) or self._as_text(last_progress.get("current_iter_image")),
                result_json=self._as_text(calibration.get("result_json")) or self._as_text(last_progress.get("result_json")),
                best_image=self._as_text(calibration.get("best_image")),
                best_score_image=self._as_text(calibration.get("best_score_image")),
                best_overlay_image=self._as_text(calibration.get("best_overlay_image")),
            )
            final_status = str(entry.get("status") or status or "finished")
            self._set_camera_progress_state(
                camera_name,
                final_status,
                finalize=final_status in {"finished", "failed", "stopped"},
            )
            self.output_panel.update_camera_result(result)
            iter_score = self._as_float(calibration.get("current_iter_score"))
            if iter_score is not None:
                self._camera_progress_current_score[camera_name] = f"{iter_score:.2f}"
        self._refresh_calibration_progress()

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

    def _merge_camera_task_best_progress(self, camera_name: str, progress: dict) -> dict[str, object]:
        merged = dict(progress)
        current_score = self._as_float(progress.get("best_score"))
        previous = self._camera_task_best_progress.get(camera_name)
        previous_score = self._as_float(previous.get("best_score")) if previous else None
        use_current = previous is None
        if not use_current and current_score is not None:
            if previous_score is None or current_score < previous_score:
                use_current = True
            elif previous_score is not None and abs(current_score - previous_score) <= 1e-12:
                use_current = False
        if use_current:
            selected = dict(progress)
        else:
            selected = dict(previous or {})
            for key in ("best_score_image", "best_overlay_image", "best_image", "result_json", "live_log"):
                if not self._as_text(selected.get(key)) and self._as_text(progress.get(key)):
                    selected[key] = progress.get(key)
        if current_score is not None and self._as_float(selected.get("best_score")) is None:
            selected["best_score"] = current_score
        self._camera_task_best_progress[camera_name] = selected
        merged.update(selected)
        return merged

    @Slot(str)
    def _on_runtime_line(self, line: str) -> None:
        self.output_panel.append_log(line, source="runtime")
        text = line.strip()
        if text:
            self._runtime_recent_lines.append(text)
            if self._should_surface_status_line(text, source="runtime"):
                self._append_status_summary_line(text)

    @Slot(str)
    def _on_calibration_line(self, line: str) -> None:
        text = line.strip()
        if text.startswith(("ORCHESTRATION_SUMMARY_JSON:", "ORCHESTRATION_EVENT_JSON:")):
            return
        self.output_panel.append_log(line, source="calibration")
        if text:
            self._calibration_recent_lines.append(text)
            if self._should_surface_status_line(text, source="calibration"):
                self._append_status_summary_line(text)

    @staticmethod
    def _build_failure_summary(title: str, lines) -> str:
        details: list[str] = []
        seen: set[str] = set()
        for raw_line in lines:
            text = str(raw_line or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            details.append(text)
            if len(details) >= 6:
                break
        if not details:
            return title
        return "\n".join([title, *details])

    @staticmethod
    def _should_surface_status_line(text: str, *, source: str) -> bool:
        lowered = text.casefold()
        if not lowered:
            return False
        if lowered.startswith(("orchestration_event_json:", "orchestration_summary_json:", "cmapi_control_summary_json:", "calibration_summary_json:", "calibration_progress_json:", "precheck_result_json:")):
            return False
        key_tokens = (
            "prepare",
            "bootstrap",
            "carmaker",
            "ipg-movie",
            "movie",
            "active sensor",
            "best score",
            "result json",
            "rounds output dir",
            "run stats",
            "calibration failed",
            "runtime process error",
            "timed out",
        )
        if any(token in lowered for token in key_tokens):
            return True
        if source == "calibration" and any(token in lowered for token in ("best image", "best overlay image", "completed rounds")):
            return True
        return False
