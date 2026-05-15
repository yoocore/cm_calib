from __future__ import annotations

import ctypes
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QCoreApplication, QTimer, Slot
from PySide6.QtWidgets import QMainWindow, QMessageBox, QSplitter, QVBoxLayout, QWidget

from gui_app.models.state import AppStatus, ApplicationState, CalibrationLaunchConfig, CameraResult
from gui_app.services.calibration_service import CalibrationService
from gui_app.services.config_service import ConfigService
from gui_app.services.precheck_service import PrecheckService
from gui_app.services.runtime_service import RuntimeService
from gui_app.services.static_vehicle_reader import resolve_vehicle_info

from gui_app.widgets.calibration_panel import CalibrationPanel
from gui_app.widgets.cm_settings_panel import CmSettingsPanel
from gui_app.widgets.output_panel import OutputPanel
from gui_app.widgets.sensor_progress_panel import SensorProgressPanel


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = project_root.resolve()
        self._runtime_mode: str | None = None
        self._pending_launch: CalibrationLaunchConfig | None = None
        self._last_runtime_summary: dict | None = None
        self._runtime_recent_lines: deque[str] = deque(maxlen=12)
        self._status_summary_lines: deque[str] = deque(maxlen=10)
        self._health_check_active = False
        self._calibration_recent_lines: deque[str] = deque(maxlen=20)
        self._calibration_task_started_at: float | None = None
        self._camera_started_at: dict[str, float] = {}
        self._camera_elapsed_final: dict[str, float] = {}
        self._camera_progress_status: dict[str, str] = {}
        self._camera_progress_detail: dict[str, str] = {}
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

        left_mid_splitter = QSplitter(Qt.Horizontal, self)
        left_mid_splitter.addWidget(self.cm_settings_panel)
        left_mid_splitter.addWidget(self.calibration_panel)
        left_mid_splitter.setSizes([400, 400])
        left_mid_splitter.setCollapsible(0, False)
        left_mid_splitter.setCollapsible(1, False)

        left_mid_container = QWidget(self)
        left_mid_layout = QVBoxLayout(left_mid_container)
        left_mid_layout.setContentsMargins(0, 0, 0, 0)
        left_mid_layout.setSpacing(6)
        left_mid_layout.addWidget(left_mid_splitter, 1)
        left_mid_layout.addWidget(self.sensor_progress_panel)

        outer_splitter = QSplitter(Qt.Horizontal, self)
        outer_splitter.addWidget(left_mid_container)
        outer_splitter.addWidget(self.output_panel)
        outer_splitter.setSizes([800, 700])
        outer_splitter.setCollapsible(0, False)
        outer_splitter.setCollapsible(1, False)

        central_container = QWidget(self)
        central_layout = QVBoxLayout(central_container)
        central_layout.setContentsMargins(8, 8, 8, 8)
        central_layout.addWidget(outer_splitter)
        self.setCentralWidget(central_container)

        self._refresh_static_timer = QTimer(self)
        self._refresh_static_timer.setInterval(1000)
        self._refresh_static_timer.timeout.connect(self._refresh_static_info)
        self._refresh_static_timer.start()
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(3000)
        self._health_timer.timeout.connect(self._check_runtime_health)
        self._wire_signals()
        self._refresh_camera_list()
        self._apply_status(AppStatus.IDLE)
        self._set_status_summary("等待操作。")

    def _wire_signals(self) -> None:
        self.calibration_panel.start_button.clicked.connect(self._start_calibration)
        self.calibration_panel.stop_button.clicked.connect(self._stop_calibration)
        self.cm_settings_panel.precheck_clicked.connect(self._run_precheck)
        self.cm_settings_panel.generate_config_clicked.connect(self._generate_configs)
        self.calibration_panel.prepare_clicked.connect(self._prepare_runtime)
        self.calibration_panel.status_query_clicked.connect(self._query_runtime_status)
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
        try:
            info = resolve_vehicle_info(project_root, testrun)
            sensors = [s["name"] for s in info.get("sensors", [])]
            if sensors:
                self.output_panel.append_log(f"Camera sensors: {', '.join(sensors)}", source="system")
        except Exception:
            sensors = []
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
        self.calibration_panel.failure_summary.setHtml(
            f'<p style="color:#e53935;font-weight:bold;margin:0;">{text}</p>'
        )

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
        self._rebuild_sensor_progress_plan()
        self._refresh_calibration_progress()

    def _begin_calibration_progress_tracking(self) -> None:
        self._reset_calibration_progress_tracking()
        self._calibration_task_started_at = time.monotonic()
        self._refresh_calibration_progress()

    def _rebuild_sensor_progress_plan(self) -> None:
        cameras = self.cm_settings_panel.selected_cameras()
        estimated_per_camera = self.calibration_panel.estimated_per_camera_seconds() if cameras else 0
        estimated_total = estimated_per_camera * len(cameras) if cameras else 0
        self.sensor_progress_panel.reset_sensor_progress(cameras, estimated_per_camera, estimated_total)

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
        if finalize:
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

        estimated_per_camera = self.calibration_panel.estimated_per_camera_seconds()
        estimated_total = estimated_per_camera * len(cameras)
        now = time.monotonic()
        overall_credit = 0.0
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

            if status == "finished":
                progress_percent = 100
                completed_count += 1
                overall_credit += float(estimated_per_camera)
                if elapsed is None:
                    elapsed_seconds = estimated_per_camera
            elif status in {"preparing", "ready", "running"}:
                effective_elapsed = min(float(elapsed or 0.0), float(estimated_per_camera))
                overall_credit += effective_elapsed
                if estimated_per_camera > 0:
                    progress_percent = int(min(95, max(1 if effective_elapsed > 0 else 0, round((effective_elapsed / float(estimated_per_camera)) * 100))))
                else:
                    progress_percent = 0
                if status == "running" and running_camera is None:
                    running_camera = camera_name
                elif status == "preparing" and preparing_camera is None:
                    preparing_camera = camera_name
                elif status == "ready" and ready_camera is None:
                    ready_camera = camera_name
            elif status in {"failed", "stopped"}:
                effective_elapsed = min(float(elapsed or 0.0), float(estimated_per_camera))
                overall_credit += effective_elapsed
                if estimated_per_camera > 0:
                    progress_percent = int(min(99, round((effective_elapsed / float(estimated_per_camera)) * 100)))
                else:
                    progress_percent = 0
            else:
                progress_percent = 0

            self.sensor_progress_panel.set_sensor_progress(
                camera_name,
                status=status,
                progress_percent=progress_percent,
                elapsed_seconds=elapsed_seconds,
                estimated_seconds=estimated_per_camera,
                detail=detail,
            )

        if self._calibration_task_started_at is not None:
            elapsed_total_seconds = int(round(max(0.0, now - self._calibration_task_started_at)))
        else:
            elapsed_total_seconds = int(round(sum(self._camera_elapsed_final.values())))
        progress_percent = int(round((overall_credit / float(estimated_total)) * 100)) if estimated_total > 0 else 0
        self.sensor_progress_panel.set_overall_progress(
            current_camera=running_camera or preparing_camera or ready_camera,
            completed_count=completed_count,
            total_count=len(cameras),
            progress_percent=min(100, max(0, progress_percent)),
            elapsed_seconds=elapsed_total_seconds,
            estimated_total_seconds=estimated_total,
        )

    def _on_project_root_changed(self, path_text: str) -> None:
        new_root = Path(path_text.strip()).resolve() if path_text.strip() else None
        if new_root and new_root != self.config_service.project_root:
            self.output_panel.append_log(f"Project root: {new_root}", source="system")
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

        try:
            project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
            if project_root.resolve() != self.precheck_service.project_root:
                self.precheck_service = PrecheckService(project_root)
            precheck_results = self.precheck_service.run_for_cameras(launch.cameras)
            self.cm_settings_panel.update_precheck_results(precheck_results)
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

        try:
            self.output_panel.log_view.clear()
            self._set_status_summary("Calib Start 已触发，正在执行预检与运行态校验。")
            cm_install = self.calibration_panel.cm_install_path
            if cm_install is None:
                raise ValueError("CM 版本未选择，请先在中栏选择 CM 版本")
            self.calibration_service.set_cm_install(cm_install)
            if not self._is_runtime_ready_for_direct_start(launch):
                summary_text = self._build_start_requires_prepare_summary(launch)
                self.calibration_panel.set_failure_summary(summary_text)
                QMessageBox.warning(self, "Runtime Not Ready", summary_text)
                return
            launch.skip_prepare_for_first_camera = True
            self.output_panel.append_log("Calib Start will reuse the existing prepared runtime for the first camera", source="runtime")
            self._append_status_summary_line("Calib Start 将复用当前已准备的运行态。")
            self.calibration_service.start(launch)
        except Exception as exc:
            self._set_status_summary(str(exc))
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
            project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
            testrun = self.cm_settings_panel.testrun_edit.text().strip()
            if not testrun:
                raise ValueError("TestRun is required")
            selected_cameras = self.cm_settings_panel.selected_cameras()
            if not selected_cameras:
                raise ValueError("Please select at least one camera")
            self.state.selected_cameras = selected_cameras
            cm_install = self.calibration_panel.cm_install_path
            if cm_install is None:
                raise ValueError("CM 版本未选择，请先在中栏选择 CM 版本")
            self.calibration_service.set_cm_install(cm_install)
            self._runtime_mode = "prepare"
            self._set_status_summary("CM Prepare 已触发，等待运行态进程启动。")
            self.runtime_service.prepare_runtime(project_root, testrun, cameras=selected_cameras, cm_install=cm_install)
        except Exception as exc:
            self._runtime_mode = None
            self._set_status_summary(str(exc))
            QMessageBox.critical(self, "Prepare Failed", str(exc))

    @Slot()
    def _run_precheck(self) -> None:
        try:
            selected_cameras = self.cm_settings_panel.selected_cameras()
            if not selected_cameras:
                raise ValueError("Please select at least one camera")
            self.output_panel.append_log(f"Check inputs: {', '.join(selected_cameras)}", source="system")
            project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
            if project_root.resolve() != self.precheck_service.project_root:
                self.precheck_service = PrecheckService(project_root)
            results = self.precheck_service.run_for_cameras(selected_cameras)
            self.cm_settings_panel.update_precheck_results(results)
            ok_count = sum(1 for result in results if bool(result.get("ok")))
            self.output_panel.append_log(f"Check inputs: {ok_count}/{len(results)} passed", source="system")
            self._set_status_summary(f"输入检查完成：{ok_count}/{len(results)} 个 camera 通过。")
        except Exception as exc:
            self.output_panel.append_log(f"Check inputs failed: {exc}", source="system")
            self._set_status_summary(str(exc))
            QMessageBox.critical(self, "Precheck Failed", str(exc))

    @Slot()
    def _generate_configs(self) -> None:
        self.cm_settings_panel.generate_config_button.setEnabled(False)
        self.cm_settings_panel.generate_config_button.setText("Generating...")
        QCoreApplication.processEvents()
        try:
            selected_cameras = self.cm_settings_panel.selected_cameras()
            if not selected_cameras:
                raise ValueError("Please select at least one camera")
            self.output_panel.append_log(f"Generate configs: {', '.join(selected_cameras)}", source="system")
            project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
            if project_root.resolve() != self.precheck_service.project_root:
                self.precheck_service = PrecheckService(project_root)
            precheck_results = self.precheck_service.run_for_cameras(selected_cameras)
            self.cm_settings_panel.update_precheck_results(precheck_results)
            failed = [result for result in precheck_results if not result.get("ok")]
            if failed:
                raise ValueError("Input check failed; fix the reported camera inputs before generating configs")
            generated_results = self.precheck_service.generate_configs_for_cameras(selected_cameras)
            self.cm_settings_panel.update_precheck_results(generated_results)
            self.output_panel.append_log(f"Generate configs: {len(generated_results)} configs updated", source="system")
            self._set_status_summary(f"配置生成完成：{len(generated_results)} 个 camera 已更新。")
        except ModuleNotFoundError as exc:
            msg = f"缺少 Python 包: {exc.name}。请在终端运行: python -m pip install {exc.name}"
            self._set_red_failure(msg)
            QMessageBox.critical(self, "缺少依赖包", msg)
        except Exception as exc:
            self._set_red_failure(str(exc))
            self.output_panel.append_log(f"Generate configs failed: {exc}", source="system")
            QMessageBox.critical(self, "Config Generation Failed", str(exc))
        finally:
            self.cm_settings_panel.generate_config_button.setText("Generate Configs")
            self.cm_settings_panel.generate_config_button.setEnabled(True)

    def _query_runtime_status(self) -> None:
        try:
            if self.runtime_service.is_running or self.calibration_service.is_running:
                return
            if self.state.status not in {AppStatus.FINISHED, AppStatus.FAILED, AppStatus.STOPPED, AppStatus.PASSIVE}:
                return
            project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
            testrun = self.cm_settings_panel.testrun_edit.text().strip()
            if not testrun:
                raise ValueError("TestRun is required")
            cm_install = self.calibration_panel.cm_install_path
            if cm_install is None:
                raise ValueError("CM 版本未选择，请先在中栏选择 CM 版本")
            self.output_panel.append_log("Query Status triggered", source="system")
            self._runtime_mode = "status"
            self._health_check_active = False
            self._set_status_summary("正在查询运行态状态...")
            self.runtime_service.probe_status(
                project_root,
                testrun,
                verify_health=True,
                cm_install=cm_install,
            )
        except Exception as exc:
            self._runtime_mode = None
            self._set_status_summary(str(exc))
            QMessageBox.critical(self, "Status Query Failed", str(exc))

    def _check_runtime_health(self) -> None:
        if self.state.status != AppStatus.READY:
            return
        if self._health_check_active or self.runtime_service.is_running:
            return
        if self.calibration_service.is_running:
            return
        project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
        testrun = self.cm_settings_panel.testrun_edit.text().strip()
        if not testrun:
            return
        self._runtime_mode = "status"
        self._health_check_active = True
        try:
            self.runtime_service.probe_status(
                project_root,
                testrun,
                verify_health=True,
                cm_install=self.calibration_panel.cm_install_path,
            )
        except Exception:
            self._runtime_mode = None
            self._health_check_active = False

    def _apply_status(self, status: AppStatus) -> None:
        self.state.status = status
        self.calibration_panel.set_status(status.value)
        self._sync_control_states()
        if status == AppStatus.READY:
            self._health_timer.start()
        else:
            self._health_timer.stop()
            if not self.runtime_service.is_running:
                self._health_check_active = False
        if status in {AppStatus.FINISHED, AppStatus.FAILED, AppStatus.STOPPED}:
            self._calibration_task_started_at = None
            self._refresh_calibration_progress()

    def _runtime_status_probe_can_update_status(self) -> bool:
        if self.state.status == AppStatus.PREPARING:
            return False
        if self.state.status == AppStatus.RUNNING:
            return False
        if self.calibration_service.is_running:
            return False
        return True

    def _sync_control_states(self) -> None:
        runtime_busy = self.runtime_service.is_running and not self._health_check_active
        calibration_running = self.state.status == AppStatus.RUNNING
        preparing = self.state.status == AppStatus.PREPARING
        can_start = self.state.status in {AppStatus.IDLE, AppStatus.READY, AppStatus.FINISHED, AppStatus.FAILED, AppStatus.STOPPED, AppStatus.PASSIVE}
        can_query_status = (
            not runtime_busy
            and not calibration_running
            and not preparing
            and self.state.status in {AppStatus.FINISHED, AppStatus.FAILED, AppStatus.STOPPED, AppStatus.PASSIVE}
        )

        self.calibration_panel.start_button.setEnabled(can_start and not runtime_busy and not calibration_running)
        self.calibration_panel.stop_button.setEnabled(calibration_running or preparing)
        controls_enabled = not runtime_busy and not calibration_running and not preparing
        self.cm_settings_panel.precheck_button.setEnabled(controls_enabled)
        self.cm_settings_panel.set_inputs_locked(not controls_enabled)
        self.calibration_panel.set_inputs_locked(not controls_enabled)
        self.calibration_panel.status_query_button.setEnabled(can_query_status)
        if preparing or calibration_running:
            self.calibration_panel.status_query_button.setToolTip("运行态准备或标定期间不可手动查询。")
        elif self.state.status == AppStatus.READY:
            self.calibration_panel.status_query_button.setToolTip("Status=ready 时会自动进行运行态轮询。")
        elif self.state.status in {AppStatus.FINISHED, AppStatus.FAILED, AppStatus.STOPPED, AppStatus.PASSIVE}:
            self.calibration_panel.status_query_button.setToolTip("" if can_query_status else "当前有运行中的后台命令，暂不可查询。")
        else:
            self.calibration_panel.status_query_button.setToolTip("当前状态无需手动查询。")

    @Slot()
    def _on_process_started(self) -> None:
        self._calibration_recent_lines.clear()
        self._begin_calibration_progress_tracking()
        self._set_status_summary("标定进行中...\n等待编排事件与单相机结果。")
        self.calibration_panel.set_phase_label("标定进行中...")
        self._apply_status(AppStatus.RUNNING)

    @Slot(int)
    def _on_process_finished(self, exit_code: int) -> None:
        if self.state.status == AppStatus.STOPPED:
            return
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
        was_health_check = self._health_check_active
        self._health_check_active = False
        self._pending_launch = None
        self.calibration_panel.set_failure_summary(self._build_failure_summary("Runtime process error", [error_text, *self._runtime_recent_lines]))
        if self._runtime_mode == "prepare":
            self.calibration_panel.set_phase_label("CM Prepare 失败")
            self._apply_status(AppStatus.PASSIVE)
        else:
            if was_health_check:
                self._append_status_summary_line(f"运行态轮询失败：{error_text}")
            self._sync_control_states()
        self._runtime_mode = None
        if not was_health_check:
            QMessageBox.critical(self, "Runtime Process Error", error_text)

    @Slot()
    def _on_runtime_process_started(self) -> None:
        self._runtime_recent_lines.clear()
        if self._runtime_mode == "prepare":
            self._set_status_summary("CM Prepare 进行中...")
            self.output_panel.append_log(self._build_prepare_start_log(), source="runtime")
            self.output_panel.append_log(
                "CM Prepare steps: activate sensor -> sync TestRun -> bootstrap run -> reuse/start IPG-MOVIE -> wait scene ready -> initialize camera widgets/dialogs -> capture initials -> health check",
                source="runtime",
            )
            self._append_status_summary_line(self._build_prepare_start_log())
            self._append_status_summary_line(
                "Prepare steps: activate sensor -> sync TestRun -> bootstrap run -> IPG-MOVIE ready -> widgets/dialogs -> capture initials -> health check"
            )
            self.calibration_panel.set_phase_label("CM Prepare 进行中...")
            self._apply_status(AppStatus.PREPARING)
        else:
            self._sync_control_states()

    @Slot(int)
    def _on_runtime_process_finished(self, exit_code: int) -> None:
        was_health_check = self._health_check_active
        self._health_check_active = False
        if self._runtime_mode == "prepare":
            if self._pending_launch is None and exit_code != 0:
                self.calibration_panel.set_failure_summary(
                    self._build_failure_summary("Prepare failed", self._runtime_recent_lines)
                )
                self._set_status_summary(self._build_failure_summary("Prepare failed", self._runtime_recent_lines))
                self.calibration_panel.set_phase_label("")
                self._apply_status(AppStatus.PASSIVE)
            elif exit_code != 0:
                self._pending_launch = None
                if self.state.status == AppStatus.PREPARING:
                    self.calibration_panel.set_failure_summary(
                        self._build_failure_summary("Prepare failed", self._runtime_recent_lines)
                    )
                    self._set_status_summary(self._build_failure_summary("Prepare failed", self._runtime_recent_lines))
                    self.calibration_panel.set_phase_label("CM Prepare 失败")
                    self._apply_status(AppStatus.PASSIVE)
                else:
                    self._sync_control_states()
            elif self._pending_launch is not None:
                self._pending_launch = None
                self.calibration_panel.set_failure_summary(
                    self._build_failure_summary("Prepare finished but no runtime summary received", self._runtime_recent_lines)
                )
                self._set_status_summary(self._build_failure_summary("Prepare finished but no runtime summary received", self._runtime_recent_lines))
                self.calibration_panel.set_phase_label("CM Prepare 状态异常")
                self._apply_status(AppStatus.PASSIVE)
            else:
                if self.state.status == AppStatus.PREPARING:
                    self._append_status_summary_line("CM Prepare 进程已结束。")
                    self._apply_status(AppStatus.PASSIVE)
                else:
                    self._sync_control_states()
        else:
            if was_health_check and exit_code != 0:
                self._append_status_summary_line("运行态轮询进程异常结束。")
            self._sync_control_states()
        self._runtime_mode = None

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
        mode = str(payload.get("mode") or "")
        status = str(payload.get("status") or "")
        if mode == "prepare":
            self._set_status_summary(self._build_prepare_summary(payload))
            self._append_prepare_trace(payload)
            if status == "ready" and self._pending_launch is not None:
                launch = self._pending_launch
                self._pending_launch = None
                self.calibration_panel.set_phase_label("CM Prepare 完成，正在启动标定...")
                self._append_status_summary_line("CM Prepare 完成，正在启动标定。")
                try:
                    self.calibration_service.start(launch)
                except Exception as exc:
                    self.calibration_panel.set_phase_label("")
                    self._set_status_summary("Calibration start failed: " + str(exc))
                    self._apply_status(AppStatus.FAILED)
                    QMessageBox.critical(self, "Calibration Start Failed", str(exc))
                return
            self._apply_status(AppStatus.READY if status == "ready" else AppStatus.PASSIVE)
        elif mode == "status":
            if self._pending_launch is not None:
                launch = self._pending_launch
                self._pending_launch = None
                if self._is_runtime_ready_for_launch(payload, launch):
                    self._set_status_summary("运行态检查通过，开始启动标定。")
                    try:
                        self.calibration_service.start(launch)
                    except Exception as exc:
                        self._set_status_summary("Calibration start failed: " + str(exc))
                        self._apply_status(AppStatus.FAILED)
                        QMessageBox.critical(self, "Calibration Start Failed", str(exc))
                    return
                self._set_status_summary(self._build_runtime_unhealthy_summary(payload, launch))
                self._apply_status(AppStatus.PASSIVE)
            elif status:
                status_reason = self._as_text(payload.get("status_reason"))
                summary_prefix = "运行态轮询" if self._health_check_active else "运行态查询"
                summary_line = f"{summary_prefix}：status={status}"
                if status_reason and status_reason != "runtime ready":
                    summary_line += f" | {status_reason}"
                if self._runtime_status_probe_can_update_status():
                    self._append_status_summary_line(summary_line)
                    self._apply_status(AppStatus.READY if status == "ready" else AppStatus.PASSIVE)
                else:
                    self._append_status_summary_line(
                        f"{summary_line} | 保持当前 Status={self.state.status.value}"
                    )
                    self._sync_control_states()
            else:
                self._sync_control_states()
        else:
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
                "标定任务已启动。"
                + (f" output_dir={output_dir}" if output_dir else "")
            )
            for camera_name in self.state.selected_cameras:
                self.output_panel.update_camera_result(CameraResult(camera=camera_name))
        elif event_name == "camera_prepare_started":
            camera_name = str(payload.get("camera") or "")
            self.output_panel.append_log(f"{camera_name}: runtime prepare starting...", source="calibration")
            self._set_camera_progress_state(camera_name, "preparing")
            self._append_status_summary_line(f"{camera_name}: 运行态准备开始。")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="preparing"))
        elif event_name == "camera_prepare_finished":
            camera_name = str(payload.get("camera") or "")
            reused = bool(payload.get("reused_existing_runtime"))
            label = "reused existing runtime" if reused else "full prepare done"
            self.output_panel.append_log(f"{camera_name}: runtime ready ({label})", source="calibration")
            self._set_camera_progress_state(camera_name, "ready")
            self._append_status_summary_line(f"{camera_name}: 运行态已就绪。")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="ready"))
        elif event_name == "camera_run_started":
            camera_name = str(payload.get("camera") or "")
            self._set_camera_progress_state(camera_name, "running")
            self._append_status_summary_line(f"{camera_name}: 标定开始。")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="running"))
        elif event_name == "camera_run_progress":
            camera_name = str(payload.get("camera") or "")
            progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
            best_score = self._as_float(progress.get("best_score"))
            iter_index = self._as_int(progress.get("current_iter_index"))
            progress_detail = None
            if iter_index is not None:
                progress_detail = f"iter={iter_index}"
                if best_score is not None:
                    progress_detail += f", best={best_score:.4f}"
            elif best_score is not None:
                progress_detail = f"best={best_score:.4f}"
            self._set_camera_progress_state(camera_name, "running", detail=progress_detail)
            progress_line = f"{camera_name}: iter={iter_index or '?'}"
            if best_score is not None:
                progress_line += f" best={best_score:.6f}"
            self.output_panel.append_log(progress_line, source="calibration")
            self._append_status_summary_line(progress_line)
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
            self._set_camera_progress_state(camera_name, "finished", finalize=True)
            self._append_status_summary_line(f"{camera_name}: 标定完成。")
            self.output_panel.update_camera_result(CameraResult(camera=camera_name, status="finished"))
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
            self._append_status_summary_line("标定任务完成。")
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
            final_status = str(entry.get("status") or status or "finished")
            self._set_camera_progress_state(
                camera_name,
                final_status,
                finalize=final_status in {"finished", "failed", "stopped"},
            )
            self.output_panel.update_camera_result(result)
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
        self.output_panel.append_log(line, source="calibration")
        text = line.strip()
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
        if int(counts.get("carmaker_runtime", counts.get("carmaker", 0))) != 1:
            return False
        if int(counts.get("carmaker_gui", 1)) < 1:
            return False
        if int(counts.get("gui_movie", 0)) < 1:
            return False
        if not active_sensors:
            return False
        if health is not None and str(health.get("code") or "") != "ok":
            return False
        return True

    def _is_runtime_ready_for_direct_start(self, launch: CalibrationLaunchConfig) -> bool:
        payload = self._last_runtime_summary if isinstance(self._last_runtime_summary, dict) else None
        if payload is None:
            return False
        if not self._is_runtime_ready_for_launch(payload, launch):
            return False
        active_sensors = payload.get("active_sensors") if isinstance(payload.get("active_sensors"), list) else []
        first_camera = launch.cameras[0] if launch.cameras else None
        if first_camera is None:
            return False
        return first_camera in [str(sensor) for sensor in active_sensors]

    def _build_start_requires_prepare_summary(self, launch: CalibrationLaunchConfig) -> str:
        payload = self._last_runtime_summary if isinstance(self._last_runtime_summary, dict) else None
        details = ["环境状态未知，请点击 CM Prepare 准备环境。"]
        if payload is None:
            details.append("尚未获取到运行态摘要。")
            return "\n".join(details)

        status = self._as_text(payload.get("status"))
        if status and status != "ready":
            details.append(f"当前 Status = {status}")

        status_reason = self._as_text(payload.get("status_reason"))
        if status_reason and status_reason != "runtime ready":
            details.append(status_reason)

        active_sensors = payload.get("active_sensors") if isinstance(payload.get("active_sensors"), list) else []
        first_camera = launch.cameras[0] if launch.cameras else None
        if first_camera and active_sensors and first_camera not in [str(sensor) for sensor in active_sensors]:
            details.append(
                f"当前 active sensor = {', '.join(str(sensor) for sensor in active_sensors)}，"
                f"与待运行首个 camera = {first_camera} 不一致。"
            )
        elif not active_sensors:
            details.append("当前没有检测到 active sensor。")

        return "\n".join(details[:4])

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
        if int(counts.get("carmaker_runtime", counts.get("carmaker", 0))) != 1:
            details.append(f"CarMaker backend runtime count = {counts.get('carmaker_runtime', counts.get('carmaker', 0))}")
        if int(counts.get("carmaker_gui", 1)) < 1:
            details.append("CarMaker GUI (HIL.exe) is not running")
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

    def _build_prepare_summary(self, payload: dict) -> str:
        details: list[str] = []
        status = self._as_text(payload.get("status")) or "passive"
        counts = payload.get("process_counts") if isinstance(payload.get("process_counts"), dict) else {}
        active_sensors = payload.get("active_sensors") if isinstance(payload.get("active_sensors"), list) else []
        testrun_control = self._as_text(payload.get("testrun_control"))
        status_reason = self._as_text(payload.get("status_reason"))

        if testrun_control:
            details.append(f"TestRun control: {testrun_control}")
        if active_sensors:
            details.append(f"Active sensors: {', '.join(str(sensor) for sensor in active_sensors)}")
        runtime_count = int(counts.get("carmaker_runtime", counts.get("carmaker", 0)))
        gui_count = int(counts.get("carmaker_gui", 0))
        gui_movie_count = int(counts.get("gui_movie", 0))
        gpu_movie_count = int(counts.get("gpusensor_movie", 0))
        if counts:
            details.append(
                "Processes: "
                f"CarMaker GUI={gui_count}, backend={runtime_count}, "
                f"GUI Movie={gui_movie_count}, GPUSensor Movie={gpu_movie_count}"
            )

        if status == "ready":
            return "\n".join([
                "CM Prepare 成功，运行态已就绪。",
                *details[:5],
            ])

        if status_reason and status_reason != "runtime ready":
            details.insert(0, status_reason)
        if not details:
            details.append("prepare did not return a ready state")
        return "\n".join([
            "CM Prepare 未达到就绪状态。",
            *details[:6],
        ])

    def _build_prepare_start_log(self) -> str:
        project_root = Path(self.cm_settings_panel.project_root_edit.text().strip() or self.project_root)
        testrun = self.cm_settings_panel.testrun_edit.text().strip() or "<unset>"
        selected_cameras = self.cm_settings_panel.selected_cameras()
        camera_text = ", ".join(selected_cameras) if selected_cameras else "<none>"
        return (
            "CM Prepare started: "
            f"project={project_root} | testrun={testrun} | cameras={camera_text}"
        )

    def _append_prepare_trace(self, payload: dict) -> None:
        lines: list[str] = []

        project_root = self._as_text(payload.get("project_root"))
        testrun = self._as_text(payload.get("testrun"))
        vehicle = self._as_text(payload.get("vehicle"))
        target_parts = [part for part in (
            f"project={project_root}" if project_root else None,
            f"testrun={testrun}" if testrun else None,
            f"vehicle={vehicle}" if vehicle else None,
        ) if part]
        if target_parts:
            lines.append("Prepare target: " + " | ".join(target_parts))

        sensor_activation = payload.get("sensor_activation") if isinstance(payload.get("sensor_activation"), dict) else None
        if sensor_activation is not None:
            sensor_name = self._as_text(sensor_activation.get("selected_sensor_name")) or "<unknown>"
            sensor_index = sensor_activation.get("selected_sensor_index")
            changed = "yes" if bool(sensor_activation.get("changed")) else "no"
            index_text = f"Sensor.{sensor_index}" if sensor_index is not None else "Sensor.?"
            lines.append(
                f"Prepare sensor activation: {sensor_name} ({index_text}.Active=1, changed={changed})"
            )

        selected_testrun = self._as_text(payload.get("selected_testrun"))
        if selected_testrun:
            lines.append(f"Prepare GUI TestRun selection: {selected_testrun}")

        bootstrap = payload.get("testrun_bootstrap") if isinstance(payload.get("testrun_bootstrap"), dict) else None
        if bootstrap is not None:
            bootstrap_label = self._as_text(bootstrap.get("label")) or self._as_text(payload.get("testrun_control")) or "unknown"
            bootstrap_testrun = self._as_text(bootstrap.get("testrun")) or self._as_text(payload.get("bootstrapped_testrun")) or "<unknown>"
            lines.append(f"Prepare bootstrap: {bootstrap_label} -> {bootstrap_testrun}")

        carmaker = payload.get("carmaker") if isinstance(payload.get("carmaker"), dict) else None
        if carmaker is not None:
            action = self._as_text(carmaker.get("action")) or "unknown"
            pid = self._as_int(carmaker.get("pid"))
            pid_text = str(pid) if pid is not None else "-"
            lines.append(f"Prepare CarMaker: action={action} | pid={pid_text}")

        movie = payload.get("movie") if isinstance(payload.get("movie"), dict) else None
        if movie is not None:
            action = self._as_text(movie.get("action")) or "unknown"
            pid = self._as_int(movie.get("pid"))
            pid_text = str(pid) if pid is not None else "-"
            lines.append(f"Prepare IPG-MOVIE: action={action} | pid={pid_text}")

            scene = movie.get("scene") if isinstance(movie.get("scene"), dict) else None
            if scene is not None:
                scene_parts = [part for part in (
                    f"mode={self._as_text(scene.get('mode'))}" if self._as_text(scene.get("mode")) else None,
                    f"camera={self._as_text(scene.get('camera_name'))}" if self._as_text(scene.get("camera_name")) else None,
                    f"size={self._as_text(scene.get('width'))}x{self._as_text(scene.get('height'))}" if self._as_text(scene.get("width")) and self._as_text(scene.get("height")) else None,
                    f"view_widget={self._as_text(scene.get('view_widget'))}" if self._as_text(scene.get("view_widget")) else None,
                ) if part]
                if scene_parts:
                    lines.append("Prepare IPG-MOVIE scene: " + " | ".join(scene_parts))

            abraxas = movie.get("abraxas") if isinstance(movie.get("abraxas"), dict) else None
            if abraxas is not None:
                before = self._as_text(abraxas.get("before")) or "unknown"
                after = self._as_text(abraxas.get("after")) or "unknown"
                lines.append(f"Prepare ABRAXAS: before={before} | after={after}")

            camera_selection = movie.get("camera_selection") if isinstance(movie.get("camera_selection"), dict) else None
            if camera_selection is not None:
                requested = self._as_text(camera_selection.get("selected")) or "<unknown>"
                current = self._as_text(camera_selection.get("current")) or "<unknown>"
                lines.append(f"Prepare camera selection: requested={requested} | current={current}")

            camera_widgets = movie.get("camera_widgets") if isinstance(movie.get("camera_widgets"), dict) else None
            if camera_widgets is not None:
                widget_parts = [part for part in (
                    f"camera={self._as_text(camera_widgets.get('after_camera'))}" if self._as_text(camera_widgets.get("after_camera")) else None,
                    f"lens={self._as_text(camera_widgets.get('after_lens'))}" if self._as_text(camera_widgets.get("after_lens")) else None,
                    f"lens_state={self._as_text(camera_widgets.get('lens_state'))}" if self._as_text(camera_widgets.get("lens_state")) else None,
                ) if part]
                if widget_parts:
                    lines.append("Prepare camera widgets: " + " | ".join(widget_parts))

            camera_dialogs = movie.get("camera_dialogs") if isinstance(movie.get("camera_dialogs"), dict) else None
            if camera_dialogs is not None:
                dialog_parts = [part for part in (
                    f"camera_state={self._as_text(camera_dialogs.get('camera_state'))}" if self._as_text(camera_dialogs.get("camera_state")) else None,
                    f"lens_state={self._as_text(camera_dialogs.get('lens_state'))}" if self._as_text(camera_dialogs.get("lens_state")) else None,
                ) if part]
                if dialog_parts:
                    lines.append("Prepare camera dialogs: " + " | ".join(dialog_parts))

        initial_capture = payload.get("config_initial_capture") if isinstance(payload.get("config_initial_capture"), dict) else None
        if initial_capture is not None:
            config_path = self._as_text(initial_capture.get("config_path"))
            captured_names = initial_capture.get("captured_names") if isinstance(initial_capture.get("captured_names"), list) else []
            capture_parts = [part for part in (
                f"config={config_path}" if config_path else None,
                f"names={', '.join(str(name) for name in captured_names)}" if captured_names else None,
            ) if part]
            if capture_parts:
                lines.append("Prepare initial capture: " + " | ".join(capture_parts))

        health = payload.get("health") if isinstance(payload.get("health"), dict) else None
        if health is not None:
            health_code = self._as_text(health.get("code")) or "unknown"
            health_message = self._as_text(health.get("message"))
            health_line = f"Prepare health check: code={health_code}"
            if health_message and health_message != health_code:
                health_line += f" | {health_message}"
            lines.append(health_line)

        active_sensors = payload.get("active_sensors") if isinstance(payload.get("active_sensors"), list) else []
        status = self._as_text(payload.get("status")) or "passive"
        status_reason = self._as_text(payload.get("status_reason"))
        status_parts = [f"status={status}"]
        if active_sensors:
            status_parts.append(f"active_sensors={', '.join(str(sensor) for sensor in active_sensors)}")
        if status_reason and status_reason != "runtime ready":
            status_parts.append(status_reason)
        lines.append("Prepare result: " + " | ".join(status_parts))

        for line in lines:
            self.output_panel.append_log(line, source="runtime")
            self._append_status_summary_line(line)

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
