from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import cmapi_testrun_control as cmctrl
from portable_runtime import build_python_subprocess_command
from runtime_config_bootstrap import load_movie_view_size_from_real_image
from cmapi_testrun_control import (
    start_simulation_via_tcl,
    stop_simulation_via_tcl,
)
from dde_health_check import run_runscript



CALIBRATION_SUMMARY_PREFIX = "CALIBRATION_SUMMARY_JSON:"
CALIBRATION_PROGRESS_PREFIX = "CALIBRATION_PROGRESS_JSON:"
ORCHESTRATION_EVENT_PREFIX = "ORCHESTRATION_EVENT_JSON:"
ORCHESTRATION_SUMMARY_PREFIX = "ORCHESTRATION_SUMMARY_JSON:"

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent / "configs"
DEFAULT_OUTPUT_ROOT = DEFAULT_PROJECT_ROOT / "SimOutput" / "camera_orchestration"


_STOP_REQUESTED = False
_ACTIVE_CHILD: Optional[subprocess.Popen[str]] = None
_EVENT_LOG_PATH: Optional[Path] = None


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _signal_handler(signum: int, _frame: object) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    if _ACTIVE_CHILD is not None and _ACTIVE_CHILD.poll() is None:
        _ACTIVE_CHILD.terminate()


def _install_signal_handlers() -> None:
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, _signal_handler)
        except (ValueError, AttributeError):
            continue


def _emit_prefixed_json(prefix: str, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(prefix, text, flush=True)
    if _EVENT_LOG_PATH is not None and prefix == ORCHESTRATION_EVENT_PREFIX:
        with _EVENT_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")


def _emit_event(task_id: str, event_type: str, **payload: Any) -> None:
    event_payload = {
        "task_id": task_id,
        "timestamp": _now_iso(),
        "event": event_type,
        **payload,
    }
    _emit_prefixed_json(ORCHESTRATION_EVENT_PREFIX, event_payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a multi-camera calibration task by orchestrating runtime camera switching and single-camera calibration runs."
    )
    parser.add_argument("--testrun", required=True, help="Path to the TestRun Info File relative to Data/TestRun.")
    parser.add_argument(
        "--camera",
        action="append",
        dest="cameras",
        default=[],
        help="Vehicle camera sensor name to calibrate. Repeat for multiple cameras.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=DEFAULT_PROJECT_ROOT,
        help="CarMaker project root. Defaults to the current repository root.",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help="Directory containing camera.<name>.json runtime configs.",
    )
    parser.add_argument(
        "--cm-install",
        type=Path,
        default=cmctrl.DEFAULT_CM_INSTALL,
        help="CarMaker installation root.",
    )
    parser.add_argument(
        "--movie-apphost",
        default=cmctrl.DEFAULT_MOVIE_APPHOST,
        help="Apphost used by GUI IPG-MOVIE readiness probing.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory for orchestration task logs and summary.",
    )
    parser.add_argument(
        "--bootstrap-running-timeout-sec",
        type=float,
        default=60.0,
        help="Maximum seconds to wait for bootstrap running state per camera switch.",
    )
    parser.add_argument(
        "--bootstrap-idle-timeout-sec",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for bootstrap idle state per camera switch.",
    )
    parser.add_argument(
        "--movie-settle-sec",
        type=float,
        default=45.0,
        help="Maximum seconds to wait for GUI IPG-MOVIE scene readiness per camera switch.",
    )
    parser.add_argument(
        "--movie-ready-poll-sec",
        type=float,
        default=1.0,
        help="Polling interval used while waiting for GUI IPG-MOVIE scene readiness.",
    )
    parser.add_argument(
        "--health-check-after-switch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run a read-only Movie remote-control health check after each camera switch.",
    )
    parser.add_argument("--health-check-attempts", type=int, default=2)
    parser.add_argument("--health-check-timeout-sec", type=float, default=2.5)
    parser.add_argument("--health-check-settle-sec", type=float, default=0.3)
    parser.add_argument("--campaign-rounds", type=int, default=1)
    parser.add_argument("--multi-start-count", type=int, default=0)
    parser.add_argument("--multi-start-iters", type=int, default=None)
    parser.add_argument("--multi-start-jitter-steps", type=float, default=2.0)
    parser.add_argument("--multi-start-seed", type=int, default=20260429)
    parser.add_argument("--skip-prepare-for-first-camera", action="store_true")
    parser.add_argument("--explore-then-refine", action="store_true")
    parser.add_argument("--refine-iters", type=int, default=None)
    parser.add_argument("--resume-from-result", action="store_true")
    parser.add_argument("--verbose-dde-diag", action="store_true")
    return parser.parse_args()


def _normalize_camera_names(raw_names: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        camera_name = str(raw_name).strip()
        if not camera_name:
            continue
        key = camera_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(camera_name)
    if not names:
        raise ValueError("At least one --camera value is required")
    return names


def _resolve_config_path(config_dir: Path, camera_name: str) -> Path:
    candidate = (config_dir / f"camera.{camera_name}.json").resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"Config not found for camera {camera_name!r}: {candidate}")
    return candidate


def _load_movie_view_size(config_path: Path) -> tuple[int, int] | None:
    return load_movie_view_size_from_real_image(config_path)


def _append_optional_arg(command: list[str], name: str, value: Optional[object]) -> None:
    if value is None:
        return
    command.extend([name, str(value)])


def _build_camera_command(args: argparse.Namespace, config_path: Path) -> list[str]:
    script_path = Path(__file__).resolve().with_name("camera_calibration.py")
    command = build_python_subprocess_command(
        script_path,
        [
        "--config",
        str(config_path),
        "--campaign-rounds",
        str(args.campaign_rounds),
        "--multi-start-count",
        str(args.multi_start_count),
        "--multi-start-jitter-steps",
        str(args.multi_start_jitter_steps),
        "--multi-start-seed",
        str(args.multi_start_seed),
        "--print-summary-json",
        "--print-progress-json",
        ],
    )
    _append_optional_arg(command, "--multi-start-iters", args.multi_start_iters)
    _append_optional_arg(command, "--refine-iters", args.refine_iters)
    if args.explore_then_refine:
        command.append("--explore-then-refine")
    if args.resume_from_result:
        command.append("--resume-from-result")
    if args.verbose_dde_diag:
        command.append("--verbose-dde-diag")
    return command


def _classify_health_or_raise(summary: dict[str, Any]) -> dict[str, Any]:
    classification = cmctrl.classify_gui_movie_send_health(summary)
    if not classification.get("all_ok"):
        raise RuntimeError(
            "Movie remote-control health check failed: "
            f"{classification.get('code')} {classification.get('summary')}"
        )
    return classification


def _prepare_runtime_for_camera(
    args: argparse.Namespace,
    project_root: Path,
    testrun_rel_path: Path,
    camera_name: str,
    config_path: Path,
    movie_view_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    # --- Step 0: Ensure CarMaker is running (sync_gui/bootstrap both need it) ---
    existing = cmctrl.list_carmaker_processes()
    _fresh_start = not existing
    if not existing:
        executable = cmctrl.resolve_carmaker_executable(args.cm_install.resolve())
        print(f"Starting CarMaker: {executable} -projectdir {project_root}")
        subprocess.Popen(
            [str(executable), "-projectdir", str(project_root)],
            cwd=str(executable.parent),
        )
        cmctrl.wait_for_carmaker_tcleval_ready(timeout_sec=60.0)
        print("CarMaker started and TclEval ready.")
    # --- Step 1: Activate sensor & sync TestRun in CarMaker GUI ---
    vehicle_path, vehicle_key = cmctrl.resolve_vehicle_path(project_root, testrun_rel_path)
    activation = cmctrl.activate_single_vehicle_sensor(vehicle_path, camera_name)
    selected_testrun = cmctrl.sync_gui_testrun_selection(project_root, testrun_rel_path)
    # --- sync_gui re-initializes IPG-MOVIE (re-registers CheckViewPort), so re-guard ---
    cmctrl.disable_checkviewport_recursion()
    # --- Step 2: StartSim / StopSim (bootstrap the TestRun for Movie) ---
    carmaker_pid, bootstrap_testrun = cmctrl.bootstrap_testrun_for_movie_via_cmapi_sync(
        project_root=project_root,
        testrun_rel_path=testrun_rel_path,
        running_timeout_sec=float(args.bootstrap_running_timeout_sec),
        idle_timeout_sec=float(args.bootstrap_idle_timeout_sec),
    )
    # --- bootstrap's internal sync_gui re-registers CheckViewPort, so re-guard ---
    cmctrl.disable_checkviewport_recursion()
    # --- Step 5: Ensure IPG-MOVIE GUI instance is running ---
    # The calibration flow sends Tcl commands via `send IPG-MOVIE`
    # (View widget, camera dialog, capture, etc.). A GPUSensor-only Movie
    # does NOT have these Tcl GUI commands, causing "dde command failed".
    # Start a GUI Movie alongside any existing GPUSensor movie rather than
    # killing the GPUSensor (which would break gpusensor_ping health checks).
    if not cmctrl.list_gui_movie_processes():
        existing_gui_pids = {int(p["ProcessId"]) for p in cmctrl.list_gui_movie_processes()}
        cmd = cmctrl.build_gui_movie_command(
            args.cm_install.resolve(),
            str(args.movie_apphost),
            project_root,
            carmaker_pid,
        )
        subprocess.Popen(cmd, cwd=str((args.cm_install.resolve() / "GUI").resolve()))
        new_gui_pid = cmctrl.wait_for_gui_movie_pid(existing_gui_pids)
        print(f"Started GUI Movie (PID {new_gui_pid}) alongside existing Movie stack")
        # --- Movie start re-registers CheckViewPort, re-guard ---
        cmctrl.disable_checkviewport_recursion()
    # --- Step 6: Wait for Movie scene ready ---
    # Fresh-start (Step 0 just launched CarMaker): Movie's DDE service may take
    # 20-40 seconds to register after first launch. Use a longer timeout so the
    # scene-ready probe doesn't fail with "dde command failed" before Movie DDE is ready.
    # Re-use path (no _fresh_start): Movie already running, 45s default is sufficient.
    _movie_settle = max(float(args.movie_settle_sec), 120.0) if _fresh_start else float(args.movie_settle_sec)
    movie_scene = cmctrl.wait_for_movie_scene_ready(
        cm_install=args.cm_install.resolve(),
        movie_apphost=str(args.movie_apphost),
        project_root=project_root,
        carmaker_pid=carmaker_pid,
        timeout_sec=_movie_settle,
        poll_interval_sec=float(args.movie_ready_poll_sec),
    )
    # Camera select first, then ABRAXAS (before View::SetSize to avoid Scene::On_Load race)
    camera_selection = cmctrl.ensure_movie_camera_selected(
        activation["ipgmovie_sensor_label"],
        timeout_sec=float(args.health_check_timeout_sec),
    )
    abraxas = cmctrl.ensure_movie_abraxas_enabled(timeout_sec=float(args.health_check_timeout_sec))
    # Cancel render timer before View::SetSize to prevent C++ ConfigFBO race
    try:
        cmctrl.cancel_movie_updateview_timer(timeout_sec=5.0)
    except Exception:
        pass
    if movie_view_size is not None:
        view_width, view_height = movie_view_size
        applied_view = cmctrl.ensure_movie_view_size(view_width, view_height)
        movie_scene["width"] = str(view_width)
        movie_scene["height"] = str(view_height)
        movie_scene["view_widget"] = str(applied_view.get("widget") or "")
        movie_scene["mode"] = str(applied_view.get("mode") or movie_scene.get("mode") or "")
    movie_scene["camera_name"] = str(camera_selection.get("current") or movie_scene.get("camera_name") or "")
    camera_widgets = cmctrl.ensure_movie_camera_widgets(timeout_sec=float(args.health_check_timeout_sec))
    # --- Step 8: Capture initial parameter values ---
    config_initial_capture = cmctrl.capture_initial_values_to_config(config_path)
    # --- Step 9: Health check ---
    health_classification: Optional[dict[str, Any]] = None
    if args.health_check_after_switch:
        health_summary = cmctrl.run_movie_send_health_check(
            attempts=int(args.health_check_attempts),
            timeout_sec=float(args.health_check_timeout_sec),
            settle_sec=float(args.health_check_settle_sec),
        )
        # FBO probe is diagnostic-only: Win32 capture does not require FBO.
        # Camera switching triggers C++ Configure->ConfigFBO which temporarily
        # corrupts FBO; killing processes on FBO failure does more harm than good.
        target_status = (health_summary.get("classification") or {}).get("target_status") or {}
        if target_status.get("ipg_movie_fbo_ok") is False:
            print(
                "[INFO] IPG-MOVIE FBO probe failed (non-fatal). "
                "Win32 capture does not require FBO; continuing."
            )
            health_classification = {
                "all_ok": True,
                "code": "ok_fbo_unavailable",
                "message": "FBO unavailable (probe failed) but Win32 capture is used.",
                "target_status": target_status,
            }
        else:
            health_classification = _classify_health_or_raise(health_summary)

    # --- Install delete-trace on CheckViewPort for auto-re-guard on unknown re-registrations ---
    cmctrl.wrap_checkviewport()

    return {
        "vehicle_path": str(vehicle_path),
        "vehicle_key": vehicle_key,
        "selected_testrun": selected_testrun,
        "bootstrap_testrun": bootstrap_testrun,
        "activation": activation,
        "carmaker_pid": carmaker_pid,
        "movie_scene": movie_scene,
        "abraxas": abraxas,
        "camera_selection": camera_selection,
        "camera_widgets": camera_widgets,
        "config_initial_capture": config_initial_capture,
        "health": health_classification,
    }


def _reuse_existing_runtime_for_camera(
    args: argparse.Namespace,
    project_root: Path,
    testrun_rel_path: Path,
    camera_name: str,
    config_path: Path,
    movie_view_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    vehicle_path, vehicle_key = cmctrl.resolve_vehicle_path(project_root, testrun_rel_path)
    status_summary = cmctrl.build_status_summary(
        project_root=project_root,
        cm_install=args.cm_install.resolve(),
        testrun_rel_path=testrun_rel_path,
        vehicle_path=vehicle_path,
        vehicle_key=vehicle_key,
        camera_sensor=camera_name,
        health_check_after_start=bool(args.health_check_after_switch),
        health_check_attempts=int(args.health_check_attempts),
        health_check_timeout_sec=float(args.health_check_timeout_sec),
        health_check_settle_sec=float(args.health_check_settle_sec),
    )

    # --- Check for FBO corruption; if detected, restart with clean processes ---
    health = status_summary.get("health") or {}
    target_status = health.get("target_status") or {}
    if target_status.get("ipg_movie_fbo_ok") is False:
        print(
            "IPG-MOVIE FBO corrupted (ipg_movie_fbo_ok=False). "
            "Killing all stale processes and starting fresh."
        )
        cmctrl.kill_all_processes()
        return _prepare_runtime_for_camera(
            args, project_root, testrun_rel_path, camera_name, config_path,
            movie_view_size=movie_view_size,
        )

    if str(status_summary.get("status") or "") != "ready":
        raise RuntimeError(
            "Current runtime is not ready for direct calibration start: "
            f"{status_summary.get('status_reason') or 'unknown reason'}"
        )

    active_sensors = status_summary.get("active_sensors") if isinstance(status_summary.get("active_sensors"), list) else []
    if camera_name not in [str(sensor) for sensor in active_sensors]:
        raise RuntimeError(
            f"Current runtime active sensor does not match first camera {camera_name!r}: {active_sensors!r}"
        )

    selected_testrun = cmctrl.sync_gui_testrun_selection(project_root, testrun_rel_path)
    # --- sync_gui re-initializes IPG-MOVIE (re-registers CheckViewPort), so re-guard ---
    cmctrl.disable_checkviewport_recursion()
    # --- Install re-entrant guard on CheckViewPort + delete-trace for persistence ---
    cmctrl.wrap_checkviewport()
    abraxas = cmctrl.ensure_movie_abraxas_enabled(timeout_sec=float(args.health_check_timeout_sec))
    camera_selection = cmctrl.ensure_movie_camera_selected(
        f"CAMERA_RSI-SENSOR Vhcl.{camera_name}",
        timeout_sec=float(args.health_check_timeout_sec),
    )
    camera_widgets = cmctrl.ensure_movie_camera_widgets(timeout_sec=float(args.health_check_timeout_sec))
    config_initial_capture = cmctrl.capture_initial_values_to_config(config_path)

    return {
        "vehicle_path": str(vehicle_path),
        "vehicle_key": vehicle_key,
        "selected_testrun": selected_testrun,
        "bootstrap_testrun": None,
        "activation": {
            "vehicle_path": str(vehicle_path),
            "selected_sensor_name": camera_name,
            "selected_sensor_index": None,
            "ipgmovie_sensor_label": f"CAMERA_RSI-SENSOR Vhcl.{camera_name}",
            "changed": False,
        },
        "carmaker_pid": None,
        "movie_scene": {},
        "abraxas": abraxas,
        "camera_selection": camera_selection,
        "camera_widgets": camera_widgets,
        "config_initial_capture": config_initial_capture,
        "health": status_summary.get("health"),
        "reused_existing_runtime": True,
        "status_summary": status_summary,
    }


def _run_single_camera_process(
    *,
    task_id: str,
    camera_name: str,
    command: list[str],
    working_dir: Path,
) -> dict[str, Any]:
    global _ACTIVE_CHILD

    _emit_event(task_id, "camera_run_started", camera=camera_name, command=command)
    process = subprocess.Popen(
        command,
        cwd=str(working_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    _ACTIVE_CHILD = process
    summary_payload: Optional[dict[str, Any]] = None
    recent_lines: deque[str] = deque(maxlen=15)

    assert process.stdout is not None
    for line in process.stdout:
        text = line.rstrip("\r\n")
        print(f"[{camera_name}] {text}", flush=True)
        recent_lines.append(text)
        if text.startswith(CALIBRATION_SUMMARY_PREFIX):
            _, _, raw_json = text.partition(":")
            raw_json = raw_json.strip()
            if raw_json:
                summary_payload = json.loads(raw_json)
        elif text.startswith(CALIBRATION_PROGRESS_PREFIX):
            _, _, raw_json = text.partition(":")
            raw_json = raw_json.strip()
            if raw_json:
                progress_payload = json.loads(raw_json)
                _emit_event(
                    task_id,
                    "camera_run_progress",
                    camera=camera_name,
                    progress=progress_payload,
                )

    return_code = process.wait()
    _ACTIVE_CHILD = None
    if _STOP_REQUESTED:
        raise KeyboardInterrupt("Stop requested during calibration task")
    if return_code != 0:
        tail = "\n".join(recent_lines)
        raise RuntimeError(
            f"camera_calibration.py exited with code {return_code} for camera {camera_name}.\n{tail}\n"
            f"See run.log in the camera output directory for the full log."
        )
    if summary_payload is None:
        raise RuntimeError(f"Missing {CALIBRATION_SUMMARY_PREFIX} line for camera {camera_name}")

    _emit_event(
        task_id,
        "camera_run_finished",
        camera=camera_name,
        best_score=summary_payload.get("best_score"),
        result_json=summary_payload.get("result_json"),
    )
    return summary_payload


def _task_output_dir(requested_output_dir: Optional[Path]) -> Path:
    if requested_output_dir is not None:
        return requested_output_dir.resolve()
    task_name = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    candidate = (DEFAULT_OUTPUT_ROOT / task_name).resolve()
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        candidate = (DEFAULT_OUTPUT_ROOT / f"{task_name}_{suffix}").resolve()
        if not candidate.exists():
            return candidate
        suffix += 1


def main() -> None:
    global _EVENT_LOG_PATH

    _install_signal_handlers()
    args = _parse_args()
    cameras = _normalize_camera_names(list(args.cameras))
    project_root = args.project_root.resolve()
    config_dir = args.config_dir.resolve()
    testrun_rel_path = cmctrl.normalize_testrun_path(project_root, args.testrun)

    # --- Kill stale processes only if NOT reusing existing runtime ---
    if not args.skip_prepare_for_first_camera:
        killed = cmctrl.kill_existing_cm_processes()
        if killed:
            print(f"Killed {len(killed)} stale process(es): {cmctrl.summarize_processes(killed)}")
            import time as _time; _time.sleep(3.0)

    output_dir = _task_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _EVENT_LOG_PATH = output_dir / "events.jsonl"

    task_id = uuid.uuid4().hex
    started_at = _now_iso()
    per_camera_results: list[dict[str, Any]] = []
    summary_path = output_dir / "task_summary.json"

    _emit_event(
        task_id,
        "task_started",
        cameras=cameras,
        output_dir=str(output_dir),
        project_root=str(project_root),
        testrun=testrun_rel_path.as_posix(),
    )

    try:
        bootstrap_configs = cmctrl.bootstrap_runtime_configs_for_cameras(
            project_root=project_root,
            camera_names=cameras,
            config_dir=config_dir,
            template_path=(config_dir / "bootstrap.template.json") if (config_dir / "bootstrap.template.json").exists() else (Path(__file__).resolve().parent / "configs" / "bootstrap.template.json"),
            movie_dir=project_root / "Movie",
            overwrite_existing=False,
            capture_current_params=False,
        )
        _emit_event(task_id, "config_bootstrap_finished", configs=bootstrap_configs)
        for camera_name in cameras:
            if _STOP_REQUESTED:
                raise KeyboardInterrupt("Stop requested before next camera run")

            config_path = _resolve_config_path(config_dir, camera_name)
            movie_view_size = _load_movie_view_size(config_path)

            # Retry once on render freeze: kill all, re-prepare, re-run
            _cam_retry = False
            while True:
                try:
                    _emit_event(task_id, "camera_prepare_started", camera=camera_name, config_path=str(config_path))
                    cmctrl.disable_checkviewport_recursion()
                    try:
                        if _cam_retry:
                            # On retry, always re-prepare fresh (processes were killed)
                            runtime_state = _prepare_runtime_for_camera(
                                args, project_root, testrun_rel_path, camera_name, config_path,
                                movie_view_size=movie_view_size,
                            )
                        elif camera_name == cameras[0] and args.skip_prepare_for_first_camera:
                            runtime_state = _reuse_existing_runtime_for_camera(
                                args, project_root, testrun_rel_path, camera_name, config_path,
                                movie_view_size=movie_view_size,
                            )
                        else:
                            runtime_state = _prepare_runtime_for_camera(
                                args, project_root, testrun_rel_path, camera_name, config_path,
                                movie_view_size=movie_view_size,
                            )
                        _emit_event(
                            task_id, "camera_prepare_finished",
                            camera=camera_name,
                            selected_sensor=runtime_state["activation"]["selected_sensor_name"],
                            vehicle_path=runtime_state["vehicle_path"],
                            carmaker_pid=runtime_state["carmaker_pid"],
                            reused_existing_runtime=bool(runtime_state.get("reused_existing_runtime")),
                        )

                        '# Removed wm lower — ineffective and triggers ConfigureNotify → NaN'

                        start_simulation_via_tcl(
                            running_timeout_sec=float(args.bootstrap_running_timeout_sec),
                            probe_name=f"start_sim_pre_calib_{camera_name}",
                        )
                        try:
                            cmctrl.sync_gui_testrun_selection(project_root, testrun_rel_path)
                            cmctrl.disable_checkviewport_recursion()
                            calibration_summary = _run_single_camera_process(
                                task_id=task_id, camera_name=camera_name,
                                command=_build_camera_command(args, config_path),
                                working_dir=Path(__file__).resolve().parent,
                            )
                        finally:
                            stop_simulation_via_tcl(
                                idle_timeout_sec=float(args.bootstrap_idle_timeout_sec),
                                probe_name=f"stop_sim_post_calib_{camera_name}",
                            )
                    finally:
                        cmctrl.restore_checkviewport()
                except RuntimeError as _cam_err:
                    if _cam_retry:
                        raise  # Second attempt also failed
                    _err_str = str(_cam_err)
                    if "rendering frozen" not in _err_str and "View(FBO)" not in _err_str and "Initial evaluation aborted" not in _err_str:
                        raise  # Non-recoverable error, abort immediately
                    print(f"[recovery] {camera_name}: render freeze detected. "
                          f"Killing all processes and retrying once...")
                    cmctrl.kill_all_processes()
                    _cam_retry = True
                    continue  # Retry with fresh processes

                # Success — add result and move to next camera
                per_camera_results.append({
                    "camera": camera_name,
                    "config_path": str(config_path),
                    "runtime": runtime_state,
                    "calibration": calibration_summary,
                    "status": "finished",
                })
                break

        summary_payload = {
            "task_id": task_id,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "status": "finished",
            "project_root": str(project_root),
            "testrun": testrun_rel_path.as_posix(),
            "output_dir": str(output_dir),
            "cameras": cameras,
            "per_camera": per_camera_results,
        }
        summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _emit_prefixed_json(ORCHESTRATION_SUMMARY_PREFIX, summary_payload)
    except KeyboardInterrupt as exc:
        summary_payload = {
            "task_id": task_id,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "status": "stopped",
            "error": str(exc),
            "project_root": str(project_root),
            "testrun": testrun_rel_path.as_posix(),
            "output_dir": str(output_dir),
            "cameras": cameras,
            "per_camera": per_camera_results,
        }
        summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _emit_event(task_id, "task_stopped", error=str(exc))
        _emit_prefixed_json(ORCHESTRATION_SUMMARY_PREFIX, summary_payload)
        raise SystemExit(130) from exc
    except Exception as exc:
        summary_payload = {
            "task_id": task_id,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "status": "failed",
            "error": str(exc),
            "project_root": str(project_root),
            "testrun": testrun_rel_path.as_posix(),
            "output_dir": str(output_dir),
            "cameras": cameras,
            "per_camera": per_camera_results,
        }
        summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _emit_event(task_id, "task_failed", error=str(exc))
        _emit_prefixed_json(ORCHESTRATION_SUMMARY_PREFIX, summary_payload)
        raise


if __name__ == "__main__":
    main()