from __future__ import annotations

from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QCoreApplication, QTimer, Slot
from PySide6.QtWidgets import QMainWindow, QMessageBox, QSplitter, QWidget

from gui_app.models.state import AppStatus, ApplicationState, CalibrationLaunchConfig, CameraResult
from gui_app.services.calibration_service import CalibrationService
from gui_app.services.config_service import ConfigService
from gui_app.services.precheck_service import PrecheckService
from gui_app.services.runtime_service import RuntimeService
from gui_app.services.static_vehicle_reader import resolve_vehicle_info

from gui_app.widgets.calibration_panel import CalibrationPanel
from gui_app.widgets.output_panel import OutputPanel
from gui_app.widgets.runtime_panel import RuntimePanel


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = project_root.resolve()
        self._runtime_mode: str | None = None
        self._pending_launch: CalibrationLaunchConfig | None = None
        self._runtime_recent_lines: deque[str] = deque(maxlen=12)
        self._health_check_active = False
        self._calibration_recent_lines: deque[str] = deque(maxlen=20)
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

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self.runtime_panel)
        splitter.addWidget(self.calibration_panel)
        splitter.addWidget(self.output_panel)
        splitter.setSizes([400, 400, 700])
        self.setCentralWidget(splitter)

        self._refresh_static_timer = QTimer(self)
        self._refresh_static_timer.setInterval(1000)
        self._refresh_static_timer.timeout.connect(self._refresh_static_info)
        self._refresh_static_timer.start()
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(1000)
        self._health_timer.timeout.connect(self._check_runtime_health)
        self._wire_signals()
        self._refresh_camera_list()
        self._apply_status(AppStatus.IDLE)

    def _wire_signals(self) -> None:
        self.calibration_panel.start_button.clicked.connect(self._start_calibration)
        self.calibration_panel.stop_button.clicked.connect(self._stop_calibration)
        self.calibration_panel.precheck_button.clicked.connect(self._run_precheck)
        self.calibration_panel.generate_config_button.clicked.connect(self._generate_configs)
        self.calibration_panel.prepare_clicked.connect(self._prepare_runtime)
        self.runtime_panel.project_root_changed.connect(self._on_project_root_changed)
        self.runtime_panel.testrun_changed.connect(self._on_testrun_changed)

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
        project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
        testrun = self.runtime_panel.testrun_edit.text().strip()
        if project_root and testrun:
            try:
                info = resolve_vehicle_info(project_root, testrun)
                sensors = [s["name"] for s in info.get("sensors", [])]
                if sensors:
                    self.calibration_panel.set_cameras(sensors)
                    return
            except Exception:
                pass
        self.calibration_panel.set_cameras(self.config_service.list_cameras())

    def _refresh_static_info(self) -> None:
        project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
        testrun = self.runtime_panel.testrun_edit.text().strip()
        if not testrun:
            self.runtime_panel.clear_sensor_list()
            return
        try:
            info = resolve_vehicle_info(project_root, testrun)
            self.runtime_panel.vehicle_label.setText(info["vehicle_key"])
            self.runtime_panel.update_sensor_list(info["sensors"])
        except Exception:
            self.runtime_panel.vehicle_label.setText("-")
            self.runtime_panel.clear_sensor_list()

    def _set_red_failure(self, text: str) -> None:
        self.calibration_panel.failure_summary.setHtml(
            f'<p style="color:#e53935;font-weight:bold;margin:0;">{text}</p>'
        )

    def _on_project_root_changed(self, path_text: str) -> None:
        new_root = Path(path_text.strip()).resolve() if path_text.strip() else None
        if new_root and new_root != self.config_service.project_root:
            self.config_service = ConfigService(new_root)
            self.precheck_service = PrecheckService(new_root)
        self._refresh_camera_list()

    def _on_testrun_changed(self, _text: str) -> None:
        self._refresh_camera_list()

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
        )

    @staticmethod
    def _spin_value_or_none(widget) -> int | None:
        value = int(widget.value())
        return None if value <= 0 else value

    @Slot()
    def _start_calibration(self) -> None:
        try:
            launch = self._build_launch_config()
        except Exception as exc:
            self.calibration_panel.set_failure_summary(str(exc))
            QMessageBox.critical(self, "Start Failed", str(exc))
            return

        # --- Precheck ---
        try:
            project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
            if project_root.resolve() != self.precheck_service.project_root:
                self.precheck_service = PrecheckService(project_root)
            precheck_results = self.precheck_service.run_for_cameras(launch.cameras)
            self.calibration_panel.update_precheck_results(precheck_results)
            failed = [r for r in precheck_results if not r.get("ok")]
            if failed:
                messages = [str(r.get("message", "")) for r in failed]
                self.calibration_panel.set_failure_summary("Precheck failed: " + "; ".join(messages))
                QMessageBox.critical(self, "Precheck Failed", "Precheck failed. See the Precheck tree and failure summary for details.")
                return
        except Exception as exc:
            self.calibration_panel.set_failure_summary("Precheck error: " + str(exc))
            QMessageBox.critical(self, "Precheck Error", str(exc))
            return

        # --- Prepare ---
        try:
            self.output_panel.log_view.clear()
            self.calibration_panel.clear_failure_summary()
            cm_install = self.calibration_panel.cm_install_path
            if cm_install is None:
                raise ValueError("CM 版本未选择，请先在中栏选择 CM 版本")
            self.calibration_service.set_cm_install(cm_install)
            self._pending_launch = launch
            self._runtime_mode = "prepare"
            self.runtime_service.prepare_runtime(launch.project_root, launch.testrun, cameras=launch.cameras, cm_install=cm_install)
        except Exception as exc:
            self._pending_launch = None
            self._runtime_mode = None
            self.calibration_panel.set_failure_summary(str(exc))
            QMessageBox.critical(self, "Start Failed", str(exc))

    @Slot()
    def _stop_calibration(self) -> None:
        if self.state.status == AppStatus.PREPARING:
            self._pending_launch = None
            self.runtime_service.stop()
        elif self.state.status == AppStatus.RUNNING:
            self.calibration_service.stop()

    @Slot()
    def _prepare_runtime(self) -> None:
        try:
            project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
            testrun = self.runtime_panel.testrun_edit.text().strip()
            if not testrun:
                raise ValueError("TestRun is required")
            selected_cameras = self.calibration_panel.selected_cameras()
            if not selected_cameras:
                raise ValueError("Please select at least one camera")
            self.state.selected_cameras = selected_cameras
            cm_install = self.calibration_panel.cm_install_path
            if cm_install is None:
                raise ValueError("CM 版本未选择，请先在中栏选择 CM 版本")
            self.calibration_service.set_cm_install(cm_install)
            self._runtime_mode = "prepare"
            self.calibration_panel.clear_failure_summary()
            self.runtime_service.prepare_runtime(project_root, testrun, cameras=selected_cameras, cm_install=cm_install)
        except Exception as exc:
            self._runtime_mode = None
            self.calibration_panel.set_failure_summary(str(exc))
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
            self.calibration_panel.set_failure_summary(str(exc))
            QMessageBox.critical(self, "Precheck Failed", str(exc))

    @Slot()
    def _generate_configs(self) -> None:
        self.calibration_panel.generate_config_button.setEnabled(False)
        self.calibration_panel.generate_config_button.setText("Generating...")
        QCoreApplication.processEvents()
        try:
            selected_cameras = self.calibration_panel.selected_cameras()
            if not selected_cameras:
                raise ValueError("Please select at least one camera")
            project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
            if project_root.resolve() != self.precheck_service.project_root:
                self.precheck_service = PrecheckService(project_root)
            precheck_results = self.precheck_service.run_for_cameras(selected_cameras)
            self.calibration_panel.update_precheck_results(precheck_results)
            failed = [result for result in precheck_results if not result.get("ok")]
            if failed:
                raise ValueError("Input check failed; fix the reported camera inputs before generating configs")
            generated_results = self.precheck_service.generate_configs_for_cameras(selected_cameras)
            self.calibration_panel.update_precheck_results(generated_results)
            self.calibration_panel.clear_failure_summary()
        except ModuleNotFoundError as exc:
            msg = f"缺少 Python 包: {exc.name}。请在终端运行: python -m pip install {exc.name}"
            self._set_red_failure(msg)
            QMessageBox.critical(self, "缺少依赖包", msg)
        except Exception as exc:
            self._set_red_failure(str(exc))
            QMessageBox.critical(self, "Config Generation Failed", str(exc))
        finally:
            self.calibration_panel.generate_config_button.setText("Generate Configs")
            self.calibration_panel.generate_config_button.setEnabled(True)

    def _check_runtime_health(self) -> None:
        if self.runtime_service.is_running:
            return
        project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
        testrun = self.runtime_panel.testrun_edit.text().strip()
        if not testrun:
            return
        self._runtime_mode = "status"
        self._health_check_active = True
        try:
            self.runtime_service.probe_status(project_root, testrun, verify_health=True)
        except Exception:
            self._runtime_mode = None
            self._health_check_active = False

    def _apply_status(self, status: AppStatus) -> None:
        self.state.status = status
        self.calibration_panel.status_label.setText(status.value)
        self._sync_control_states()
        if status in (AppStatus.READY, AppStatus.RUNNING):
            self._health_timer.start()
        else:
            self._health_timer.stop()

    def _sync_control_states(self) -> None:
        runtime_busy = self.runtime_service.is_running
        calibration_running = self.state.status == AppStatus.RUNNING
        preparing = self.state.status == AppStatus.PREPARING
        can_start = self.state.status in {AppStatus.IDLE, AppStatus.READY, AppStatus.FINISHED, AppStatus.FAILED, AppStatus.STOPPED, AppStatus.PASSIVE}

        self.calibration_panel.start_button.setEnabled(can_start and not runtime_busy and not calibration_running)
        self.calibration_panel.stop_button.setEnabled(calibration_running or preparing)
        controls_enabled = not runtime_busy and not calibration_running and not preparing
        self.calibration_panel.precheck_button.setEnabled(controls_enabled)
        self.calibration_panel.set_inputs_locked(not controls_enabled)
        self.runtime_panel.set_inputs_locked(not controls_enabled)

    @Slot()
    def _on_process_started(self) -> None:
        self._calibration_recent_lines.clear()
        self.calibration_panel.clear_failure_summary()
        self.calibration_panel.set_phase_label("标定进行中...")
        self._apply_status(AppStatus.RUNNING)

    @Slot(int)
    def _on_process_finished(self, exit_code: int) -> None:
        if self.state.status == AppStatus.STOPPED:
            return
        if exit_code != 0:
            self.calibration_panel.set_failure_summary(self._build_failure_summary("Calibration failed", self._calibration_recent_lines))
        self.calibration_panel.set_phase_label("")
        self._apply_status(AppStatus.FINISHED if exit_code == 0 else AppStatus.FAILED)

    @Slot(str)
    def _on_process_failed(self, error_text: str) -> None:
        self.calibration_panel.set_failure_summary(self._build_failure_summary("Calibration process error", [error_text, *self._calibration_recent_lines]))
        self.calibration_panel.set_phase_label("")
        self._apply_status(AppStatus.FAILED)
        QMessageBox.critical(self, "Process Error", error_text)

    @Slot(str)
    def _on_runtime_process_failed(self, error_text: str) -> None:
        self._pending_launch = None
        self.calibration_panel.set_failure_summary(self._build_failure_summary("Runtime process error", [error_text, *self._runtime_recent_lines]))
        if self._runtime_mode == "prepare":
            self.calibration_panel.set_phase_label("CM Prepare 失败")
            self._apply_status(AppStatus.PASSIVE)
        else:
            self._sync_control_states()
        was_health_check = self._health_check_active
        self._runtime_mode = None
        self._health_check_active = False
        if not was_health_check:
            QMessageBox.critical(self, "Runtime Process Error", error_text)

    @Slot()
    def _on_runtime_process_started(self) -> None:
        self._runtime_recent_lines.clear()
        self.calibration_panel.clear_failure_summary()
        if self._runtime_mode == "prepare":
            self.output_panel.append_log("[runtime] CM Prepare uses Tcl StartSim/StopSim for the TestRun bootstrap")
            self.calibration_panel.set_phase_label("CM Prepare 进行中...")
            self._apply_status(AppStatus.PREPARING)
        else:
            self._sync_control_states()

    @Slot(int)
    def _on_runtime_process_finished(self, exit_code: int) -> None:
        if self._runtime_mode == "prepare":
            if self._pending_launch is None and exit_code != 0:
                self.calibration_panel.set_phase_label("")
                self._apply_status(AppStatus.PASSIVE)
            elif exit_code != 0:
                self._pending_launch = None
                if self.state.status == AppStatus.PREPARING:
                    self.calibration_panel.set_failure_summary(
                        self._build_failure_summary("Prepare failed", self._runtime_recent_lines)
                    )
                    self.calibration_panel.set_phase_label("CM Prepare 失败")
                    self._apply_status(AppStatus.PASSIVE)
                else:
                    self._sync_control_states()
            elif self._pending_launch is not None:
                self._pending_launch = None
                self.calibration_panel.set_failure_summary(
                    self._build_failure_summary("Prepare finished but no runtime summary received", self._runtime_recent_lines)
                )
                self.calibration_panel.set_phase_label("CM Prepare 状态异常")
                self._apply_status(AppStatus.PASSIVE)
            else:
                if self.state.status == AppStatus.PREPARING:
                    self._apply_status(AppStatus.PASSIVE)
                else:
                    self._sync_control_states()
        else:
            self._sync_control_states()
        self._runtime_mode = None
        self._health_check_active = False

    @Slot(dict)
    def _on_runtime_summary(self, payload: dict) -> None:
        self.runtime_panel.set_runtime_summary(payload)
        summary_parts = [
            f"mode={payload.get('mode')}",
            f"status={payload.get('status', payload.get('mode'))}",
        ]
        testrun_control = self._as_text(payload.get("testrun_control"))
        if testrun_control:
            summary_parts.append(f"testrun_control={testrun_control}")
        self.output_panel.append_log(f"[runtime] summary {' '.join(summary_parts)}")
        mode = str(payload.get("mode") or "")
        status = str(payload.get("status") or "")
        if mode == "prepare":
            if status == "ready":
                self.calibration_panel.clear_failure_summary()
            if status == "ready" and self._pending_launch is not None:
                launch = self._pending_launch
                self._pending_launch = None
                self.calibration_panel.set_phase_label("CM Prepare 完成，正在启动标定...")
                try:
                    self.calibration_service.start(launch)
                except Exception as exc:
                    self.calibration_panel.set_phase_label("")
                    self.calibration_panel.set_failure_summary("Calibration start failed: " + str(exc))
                    self._apply_status(AppStatus.FAILED)
                    QMessageBox.critical(self, "Calibration Start Failed", str(exc))
                return
            self._apply_status(AppStatus.READY if status == "ready" else AppStatus.PASSIVE)
        elif mode == "status":
            if self._pending_launch is not None:
                launch = self._pending_launch
                self._pending_launch = None
                if self._is_runtime_ready_for_launch(payload, launch):
                    self.calibration_panel.clear_failure_summary()
                    try:
                        self.calibration_service.start(launch)
                    except Exception as exc:
                        self.calibration_panel.set_failure_summary("Calibration start failed: " + str(exc))
                        self._apply_status(AppStatus.FAILED)
                        QMessageBox.critical(self, "Calibration Start Failed", str(exc))
                    return
                self.calibration_panel.set_failure_summary(self._build_runtime_unhealthy_summary(payload, launch))
                self._apply_status(AppStatus.PASSIVE)
            elif status:
                self._apply_status(AppStatus.READY if status == "ready" else AppStatus.PASSIVE)
            else:
                self._sync_control_states()
        else:
            self._sync_control_states()

    @Slot(dict)
    def _on_orchestration_event(self, payload: dict) -> None:
        event_name = str(payload.get("event") or "")
        if event_name == "task_started":
            output_dir = str(payload.get("output_dir") or "")
            self.state.output_dir = Path(output_dir) if output_dir else None
            self.output_panel.set_output_dir(output_dir or None)
            self.output_panel.set_log_path(str(Path(output_dir) / "events.jsonl") if output_dir else None)
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
                    live_log=self._as_text(progress.get("live_log")),
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
            self.calibration_panel.set_failure_summary(self._build_failure_summary("Calibration task failed", [self._as_text(payload.get("error")) or "Unknown failure", *self._calibration_recent_lines]))
            self._apply_status(AppStatus.FAILED)
        elif event_name == "task_stopped":
            error_text = self._as_text(payload.get("error"))
            if error_text:
                self.calibration_panel.set_failure_summary(error_text)
            self._apply_status(AppStatus.STOPPED)

    @Slot(dict)
    def _on_orchestration_summary(self, payload: dict) -> None:
        status = str(payload.get("status") or "")
        if status == "stopped":
            if payload.get("error"):
                self.calibration_panel.set_failure_summary(str(payload.get("error")))
            self._apply_status(AppStatus.STOPPED)
        elif status == "failed":
            self.calibration_panel.set_failure_summary(self._build_failure_summary("Calibration task failed", [self._as_text(payload.get("error")) or "Unknown failure", *self._calibration_recent_lines]))
            self._apply_status(AppStatus.FAILED)
        else:
            self.calibration_panel.clear_failure_summary()
            self._apply_status(AppStatus.FINISHED)

        for entry in payload.get("per_camera", []):
            if not isinstance(entry, dict):
                continue
            camera_name = str(entry.get("camera") or "")
            calibration = entry.get("calibration") if isinstance(entry.get("calibration"), dict) else {}
            result = CameraResult(
                camera=camera_name,
                status=str(entry.get("status") or status or "finished"),
                live_log=self._as_text(calibration.get("live_log")),
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

    @Slot(str)
    def _on_runtime_line(self, line: str) -> None:
        self.output_panel.append_log(line)
        text = line.strip()
        if text:
            self._runtime_recent_lines.append(text)

    @Slot(str)
    def _on_calibration_line(self, line: str) -> None:
        self.output_panel.append_log(line)
        text = line.strip()
        if text:
            self._calibration_recent_lines.append(text)

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

    def _is_runtime_ready_for_launch(self, payload: dict, launch: CalibrationLaunchConfig) -> bool:
        expected_project_root = launch.project_root.resolve()
        running_project_root_text = self._as_text(payload.get("running_projectdir"))
        running_project_root = Path(running_project_root_text).resolve() if running_project_root_text else None
        counts = payload.get("process_counts") if isinstance(payload.get("process_counts"), dict) else {}
        active_sensors = payload.get("active_sensors") if isinstance(payload.get("active_sensors"), list) else []
        health = payload.get("health") if isinstance(payload.get("health"), dict) else None

        if str(payload.get("status") or "") != "ready":
            return False
        if running_project_root is None or running_project_root != expected_project_root:
            return False
        if int(counts.get("carmaker", 0)) != 1:
            return False
        if int(counts.get("gui_movie", 0)) < 1:
            return False
        if not active_sensors:
            return False
        if health is not None and str(health.get("code") or "") != "ok":
            return False
        return True

    def _build_runtime_unhealthy_summary(self, payload: dict, launch: CalibrationLaunchConfig) -> str:
        details: list[str] = []
        testrun_control = self._as_text(payload.get("testrun_control"))
        if testrun_control:
            details.append(f"CM Prepare TestRun control: {testrun_control}")

        status_reason = self._as_text(payload.get("status_reason"))
        if status_reason and status_reason != "runtime ready":
            details.append(status_reason)

        running_project_root = self._as_text(payload.get("running_projectdir"))
        expected_project_root = launch.project_root.resolve().as_posix()
        if running_project_root and Path(running_project_root).resolve().as_posix() != expected_project_root:
            details.append(f"expected projectdir {expected_project_root}, got {Path(running_project_root).resolve().as_posix()}")

        counts = payload.get("process_counts") if isinstance(payload.get("process_counts"), dict) else {}
        if int(counts.get("carmaker", 0)) != 1:
            details.append(f"CarMaker runtime count = {counts.get('carmaker', 0)}")
        if int(counts.get("gui_movie", 0)) < 1:
            details.append("GUI Movie is not running")

        active_sensors = payload.get("active_sensors") if isinstance(payload.get("active_sensors"), list) else []
        if not active_sensors:
            details.append("no active camera sensor found")

        health = payload.get("health") if isinstance(payload.get("health"), dict) else None
        if health is not None and str(health.get("code") or "") != "ok":
            details.append(str(health.get("message") or health.get("code") or "Movie remote-control health check failed"))

        if not details:
            details.append("runtime probe did not return a ready state")
        return "\n".join([
            "Runtime is not healthy enough to start calibration. Run CM Prepare first.",
            *details[:6],
        ])