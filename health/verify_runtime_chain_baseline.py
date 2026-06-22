from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import time
from typing import Any, Optional

try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None

from health.dde_health_check import default_output_dir, render_dde_execute_script, run_check_attempt
from scripts.runtime_config_bootstrap import load_movie_view_size_from_real_image

from src.cmapi_testrun_control import (
    DEFAULT_CM_INSTALL,
    DEFAULT_CONFIG_DIR,
    DEFAULT_MOVIE_APPHOST,
    DEFAULT_MOVIE_SCENE_READY_GRACE_SEC,
    DEFAULT_PROJECT_ROOT,
    bootstrap_testrun_for_movie_via_cmapi,
    classify_gui_movie_send_health,
    cleanup,
    ensure_movie_abraxas_enabled,
    ensure_movie_camera_widgets,
    ensure_movie_view_size,
    kill_all_movie_processes,
    list_gpusensor_movie_processes,
    list_gui_movie_processes,
    normalize_sensor_name,
    normalize_testrun_path,
    resolve_vehicle_path,
    run_tcl_sim_command,
    run_movie_send_health_check,
    start_or_reuse_carmaker_for_open_movie,
    start_or_reuse_movie,
    sync_gui_testrun_selection,
    wait_for_carmaker_status,
    wait_for_movie_scene_ready,
)


SUMMARY_PREFIX = "RUNTIME_VERIFY_SUMMARY_JSON:"
SENSOR_NAME_RE = re.compile(r"^\s*Sensor\.(?P<index>\d+)\.name\s*=\s*(?P<value>.*?)\s*$")
SENSOR_ACTIVE_RE = re.compile(r"^\s*Sensor\.(?P<index>\d+)\.Active\s*=\s*(?P<value>[01])\s*$")
DEFAULT_MOVIE_QUIT_TIMEOUT_SEC = 8.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the frozen CarMaker/IPG-MOVIE runtime chain from opening CarMaker "
            "through the final read-only DDE health check, without prepare-time config generation."
        )
    )
    parser.add_argument(
        "--testrun",
        required=True,
        help="Path to the TestRun Info File relative to Data/TestRun.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=DEFAULT_PROJECT_ROOT,
        help="CarMaker project root. Defaults to the current repository root.",
    )
    parser.add_argument(
        "--cm-install",
        type=Path,
        default=DEFAULT_CM_INSTALL,
        help="CarMaker installation root. Defaults to D:/IPG/carmaker/win64-14.1.",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Host used by CMAPI application objects.",
    )
    parser.add_argument(
        "--movie-apphost",
        default=DEFAULT_MOVIE_APPHOST,
        help="Apphost used when launching GUI IPG-MOVIE.",
    )
    parser.add_argument(
        "--camera-sensor",
        default=None,
        help=(
            "Optional camera sensor to validate. If omitted, the script auto-selects the single active sensor "
            "from the Vehicle file when exactly one is active."
        ),
    )
    parser.add_argument(
        "--require-sensor-runtime",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Fail the whole run if sensor-specific checks cannot be completed. By default these checks are "
            "best-effort because the standalone verifier does not modify Vehicle activation like prepare does."
        ),
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help="Directory containing camera.<name>.json runtime configs used for real_image-sized view checks.",
    )
    parser.add_argument(
        "--testrun-control-mode",
        choices=("tcl", "tk-buttons"),
        default="tcl",
        help="Bootstrap TestRun via pure Tcl StartSim/StopSim or via CarMaker Tk button invoke semantics.",
    )
    parser.add_argument(
        "--clean-existing-processes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reset conflicting CarMaker/Movie processes before starting the validation chain.",
    )
    parser.add_argument(
        "--keep-carmaker-open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep CarMaker open after validation finishes.",
    )
    parser.add_argument(
        "--keep-movie-open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep GUI IPG-MOVIE open after validation finishes.",
    )
    parser.add_argument(
        "--startup-settle-sec",
        type=float,
        default=2.0,
        help="Seconds to wait after CarMaker startup before starting the bootstrap chain.",
    )
    parser.add_argument(
        "--bootstrap-running-timeout-sec",
        type=float,
        default=60.0,
        help="Maximum seconds to wait for the bootstrap TestRun to reach running.",
    )
    parser.add_argument(
        "--bootstrap-idle-timeout-sec",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for the bootstrap TestRun to return to idle.",
    )
    parser.add_argument(
        "--movie-settle-sec",
        type=float,
        default=45.0,
        help="Maximum seconds to wait until IPG-MOVIE reports the calibration scene is ready.",
    )
    parser.add_argument(
        "--movie-ready-poll-sec",
        type=float,
        default=1.0,
        help="Polling interval while waiting for IPG-MOVIE scene readiness.",
    )
    parser.add_argument(
        "--movie-ready-grace-sec",
        type=float,
        default=DEFAULT_MOVIE_SCENE_READY_GRACE_SEC,
        help="Grace window before send-probe failures can trigger GUI Movie recovery.",
    )
    parser.add_argument(
        "--health-check-attempts",
        type=int,
        default=2,
        help="Attempts per check for the final read-only DDE health suite.",
    )
    parser.add_argument(
        "--health-check-timeout-sec",
        type=float,
        default=8.0,
        help="Timeout per check attempt for scene, widget, and final health validations.",
    )
    parser.add_argument(
        "--health-check-settle-sec",
        type=float,
        default=0.5,
        help="Retry delay between attempts in the final DDE health suite.",
    )
    parser.add_argument(
        "--health-policy",
        choices=("movie-core", "strict"),
        default="movie-core",
        help=(
            "How to judge the final DDE health suite. movie-core requires the TclEval and GUI Movie remote-control "
            "surface to be healthy; strict additionally requires GPUSensor checks to pass."
        ),
    )
    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="Emit a machine-readable JSON summary line prefixed with RUNTIME_VERIFY_SUMMARY_JSON:.",
    )
    parser.add_argument(
        "--movie-quit-timeout-sec",
        type=float,
        default=DEFAULT_MOVIE_QUIT_TIMEOUT_SEC,
        help="Seconds to wait for Movie::Quit * to close GUI Movie and GPUSensor before falling back.",
    )
    return parser.parse_args()


def emit_summary_json(payload: dict[str, Any]) -> None:
    print(SUMMARY_PREFIX, json.dumps(payload, ensure_ascii=False, sort_keys=True))


def collect_vehicle_sensor_state_local(vehicle_path: Path) -> list[dict[str, Any]]:
    text = vehicle_path.read_text(encoding="utf-8")
    sensor_name_by_index: dict[str, str] = {}
    active_value_by_index: dict[str, bool] = {}

    for line in text.splitlines():
        name_match = SENSOR_NAME_RE.match(line)
        if name_match:
            sensor_name_by_index[name_match.group("index")] = name_match.group("value").strip()
            continue

        active_match = SENSOR_ACTIVE_RE.match(line)
        if active_match:
            active_value_by_index[active_match.group("index")] = active_match.group("value") == "1"

    sensors: list[dict[str, Any]] = []
    for sensor_index in sorted(sensor_name_by_index, key=int):
        sensor_name = sensor_name_by_index[sensor_index]
        sensors.append(
            {
                "index": int(sensor_index),
                "name": sensor_name,
                "active": bool(active_value_by_index.get(sensor_index, False)),
                "ipgmovie_sensor_label": f"CAMERA_RSI-SENSOR Vhcl.{sensor_name}",
            }
        )
    return sensors


def snapshot_movie_stack() -> dict[str, list[int]]:
    return {
        "gui": [int(proc["ProcessId"]) for proc in list_gui_movie_processes()],
        "gpu": [int(proc["ProcessId"]) for proc in list_gpusensor_movie_processes()],
    }


def stop_movie_stack_via_movie_quit(
    *,
    timeout_sec: float,
    probe_name: str,
) -> dict[str, Any]:
    before = snapshot_movie_stack()
    if not before["gui"] and not before["gpu"]:
        return {
            "mode": "movie_quit_noop",
            "before": before,
            "after": before,
            "fallback": False,
        }

    command_result = run_tcl_sim_command(
        commands=[
            "Movie::Quit *",
            "update",
            "update idletasks",
        ],
        probe_name=probe_name,
        timeout_sec=max(5.0, float(timeout_sec)),
    )

    deadline = time.monotonic() + max(0.5, float(timeout_sec))
    after = before
    while time.monotonic() < deadline:
        after = snapshot_movie_stack()
        if not after["gui"] and not after["gpu"]:
            return {
                "mode": "movie_quit",
                "before": before,
                "after": after,
                "fallback": False,
                "command_result": command_result,
            }
        time.sleep(0.2)

    killed = kill_all_movie_processes()
    fallback_after = snapshot_movie_stack()
    return {
        "mode": "movie_quit_fallback_taskkill",
        "before": before,
        "after": fallback_after,
        "fallback": True,
        "command_result": command_result,
        "fallback_killed_pids": [int(proc["ProcessId"]) for proc in killed],
    }


def resolve_target_sensor(vehicle_path: Path, requested_sensor: Optional[str]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    sensors = collect_vehicle_sensor_state_local(vehicle_path)
    if requested_sensor:
        target_name = normalize_sensor_name(requested_sensor)
        for sensor in sensors:
            if normalize_sensor_name(str(sensor.get("name") or "")).casefold() == target_name.casefold():
                return sensor, None
        available = ", ".join(str(sensor.get("name") or "") for sensor in sensors) or "<none>"
        raise ValueError(f"Camera sensor {requested_sensor!r} was not found in {vehicle_path.name}. Available: {available}")

    active_sensors = [sensor for sensor in sensors if sensor.get("active")]
    if len(active_sensors) == 1:
        return active_sensors[0], None
    if not active_sensors:
        return None, "no active sensor found in Vehicle file; sensor-specific checks skipped"

    names = ", ".join(str(sensor.get("name") or "") for sensor in active_sensors)
    return None, f"multiple active sensors found ({names}); pass --camera-sensor to enable sensor-specific checks"


def bootstrap_testrun_via_tk_buttons(
    expected_testrun_name: str,
    *,
    running_timeout_sec: float,
    idle_timeout_sec: float,
) -> dict[str, Any]:
    running_timeout_ms = max(1000, int(max(1.0, float(running_timeout_sec)) * 1000))
    idle_timeout_ms = max(1000, int(max(1.0, float(idle_timeout_sec)) * 1000))

    wait_for_carmaker_status("idle", 10000, probe_name="runtime_verify_tk_buttons_idle_before")
    start_invoke = run_tcl_sim_command(
        commands=[
            'if {![winfo exists .f.btn.start]} {error "missing widget .f.btn.start"}',
            ".f.btn.start invoke",
            "update",
            "update idletasks",
        ],
        probe_name="runtime_verify_tk_buttons_start_invoke",
        timeout_sec=max(10.0, float(running_timeout_sec) + 5.0),
    )
    if not start_invoke.get("ok"):
        raise RuntimeError(
            "Failed to invoke CarMaker Tcl/Tk start button: "
            f"{start_invoke.get('kind')}: {start_invoke.get('detail')}"
        )
    running = wait_for_carmaker_status(
        "running",
        running_timeout_ms,
        probe_name="runtime_verify_tk_buttons_running",
    )

    stop_invoke = run_tcl_sim_command(
        commands=[
            'if {![winfo exists .f.btn.stop]} {error "missing widget .f.btn.stop"}',
            ".f.btn.stop invoke",
            "update",
            "update idletasks",
        ],
        probe_name="runtime_verify_tk_buttons_stop_invoke",
        timeout_sec=max(10.0, float(idle_timeout_sec) + 5.0),
    )
    if not stop_invoke.get("ok"):
        raise RuntimeError(
            "Failed to invoke CarMaker Tcl/Tk stop button: "
            f"{stop_invoke.get('kind')}: {stop_invoke.get('detail')}"
        )
    idle = wait_for_carmaker_status(
        "idle",
        idle_timeout_ms,
        probe_name="runtime_verify_tk_buttons_idle",
    )

    return {
        "mode": "tk-buttons",
        "label": ".f.btn.start/.f.btn.stop invoke",
        "start_invoke": start_invoke,
        "stop_invoke": stop_invoke,
        "running": running,
        "idle": idle,
    }


def _parse_probe_detail(detail: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for chunk in str(detail or "").strip().split(";"):
        key, separator, value = chunk.partition("=")
        if separator:
            payload[key.strip()] = value.strip()
    return payload


def capture_runtime_screenshot(output_dir: Path, file_name: str) -> Optional[str]:
    if ImageGrab is None:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / file_name
    ImageGrab.grab(all_screens=True).save(screenshot_path)
    return str(screenshot_path)


def select_movie_camera_sensor_after_scene_ready(
    sensor_label: str,
    *,
    timeout_sec: float,
) -> dict[str, str]:
    target_label = str(sensor_label or "").strip()
    if not target_label:
        raise ValueError("IPG-MOVIE sensor label must not be empty")

    escaped_label = target_label.replace("\\", "\\\\").replace('"', '\\"')
    output_dir = default_output_dir() / "sensor_view_probe"
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "runtime_verify_movie_camera_select_probe"
    capture_path = output_dir / "selected_sensor_render.png"
    result = run_check_attempt(
        name=probe_name,
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        script_text=render_dde_execute_script(
            output_dir / f"{probe_name}.txt",
            "IPG-MOVIE",
            [
                'if {![info exists View(ev.view)]} {error "missing View(ev.view)"}',
                'set vno $View(ev.view)',
                f'set target "{escaped_label}"',
                'Camera::ShowSettingsDlg',
                'update',
                'update idletasks',
                'Camera::Select $target $vno',
                'update',
                'update idletasks',
                'if {![winfo exists .camera.btn.set]} {error "missing widget .camera.btn.set"}',
                '.camera.btn.set invoke',
                'update',
                'update idletasks',
                'set wi [dict get $View($vno) Width]',
                'set he [dict get $View($vno) Height]',
                'set captureFBO [FBO new $wi $he -tex rgb -noclear]',
                'set update_rc [catch {',
                '    FBO begin $captureFBO',
                '    UpdateView $vno',
                '    FBO end',
                '} update_msg]',
                'catch {FBO end}',
                'if {$update_rc != 0} {',
                '    catch {FBO delete $captureFBO}',
                '    error $update_msg',
                '}',
                'catch {image delete probeImg}',
                'image create photo probeImg -width $wi -height $he',
                'gl bindframebuffer_read $captureFBO',
                'gl readpixels 0 0 probeImg',
                f'probeImg write "{capture_path.as_posix()}" -format png',
                'catch {gl bindframebuffer_read 0}',
                'catch {FBO delete $captureFBO}',
                'if {[info exists Camera::v(Name)]} {set current $Camera::v(Name)} else {set current ""}',
                'format "state=selected;selected=%s;current=%s;view=%s;apply_invoked=1;capture_path=%s" $target $current $vno {' + capture_path.as_posix() + '}',
            ],
        ),
        timeout_sec=max(1.0, float(timeout_sec)),
    )
    if not result.get("ok"):
        raise RuntimeError(
            "Failed to select IPG-MOVIE camera sensor after scene ready: "
            f"requested={target_label}, last_detail={result.get('kind')}: {result.get('detail')}"
        )

    payload = _parse_probe_detail(str(result.get("detail") or ""))
    if payload.get("state") != "selected":
        raise RuntimeError(
            "Failed to select IPG-MOVIE camera sensor after scene ready: "
            f"requested={target_label}, last_detail={result.get('detail')}"
        )

    current_label = str(payload.get("current") or "")
    if current_label != target_label:
        raise RuntimeError(
            "Failed to select IPG-MOVIE camera sensor after scene ready: "
            f"requested={target_label}, current={current_label}, last_detail={result.get('detail')}"
        )

    payload["mode"] = "camera_select_render_verified"
    return payload


def ensure_movie_camera_dialogs_normal(*, timeout_sec: float) -> dict[str, str]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "runtime_verify_camera_dialogs_normal_probe"
    result = run_check_attempt(
        name=probe_name,
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        script_text=render_dde_execute_script(
            output_dir / f"{probe_name}.txt",
            "IPG-MOVIE",
            [
                "Camera::ShowSettingsDlg",
                "update",
                "update idletasks",
                'if {[winfo exists .camera]} { wm deiconify .camera }',
                "update",
                "update idletasks",
                'if {[winfo exists .camera.cammoddlg]} {',
                '    wm deiconify .camera.cammoddlg',
                '} elseif {[winfo exists .camera.fmore.bcammod]} {',
                '    .camera.fmore.bcammod invoke',
                '}',
                "update",
                "update idletasks",
                'set camera_exists [expr {[winfo exists .camera] ? 1 : 0}]',
                'set camera_title [expr {[winfo exists .camera] ? [wm title .camera] : ""}]',
                'set camera_state [expr {[winfo exists .camera] ? [wm state .camera] : "missing"}]',
                'set lens_exists [expr {[winfo exists .camera.cammoddlg] ? 1 : 0}]',
                'set lens_title [expr {[winfo exists .camera.cammoddlg] ? [wm title .camera.cammoddlg] : ""}]',
                'set lens_state [expr {[winfo exists .camera.cammoddlg] ? [wm state .camera.cammoddlg] : "missing"}]',
                'format "camera_exists=%s;camera_title=%s;camera_state=%s;lens_exists=%s;lens_title=%s;lens_state=%s" $camera_exists $camera_title $camera_state $lens_exists $lens_title $lens_state',
            ],
        ),
        timeout_sec=max(1.0, float(timeout_sec)),
    )
    if not result.get("ok"):
        raise RuntimeError(
            "Failed to deiconify IPG-MOVIE Camera Settings/Lens Parameters: "
            f"{result.get('kind')}: {result.get('detail')}"
        )

    payload: dict[str, str] = {}
    detail = str(result.get("detail") or "").strip()
    for chunk in detail.split(";"):
        key, separator, value = chunk.partition("=")
        if separator:
            payload[key.strip()] = value.strip()

    if payload.get("camera_exists") != "1":
        raise RuntimeError("IPG-MOVIE Camera Settings dialog is missing after deiconify probe")
    if payload.get("lens_exists") != "1":
        raise RuntimeError("IPG-MOVIE Camera Lens Parameters dialog is missing after deiconify probe")
    if payload.get("camera_state") != "normal":
        raise RuntimeError(f"IPG-MOVIE Camera Settings dialog is not normal: {payload.get('camera_state')}")
    if payload.get("lens_state") != "normal":
        raise RuntimeError(f"IPG-MOVIE Camera Lens Parameters dialog is not normal: {payload.get('lens_state')}")

    payload["mode"] = "camera_dialogs_normal"
    return payload


def evaluate_health_policy(classification: dict[str, Any], policy: str) -> tuple[bool, str]:
    if policy == "strict":
        if classification.get("all_ok"):
            return True, "strict_all_ok"
        return False, str(classification.get("code") or "strict_failed")

    target_status = classification.get("target_status") if isinstance(classification, dict) else None
    if not isinstance(target_status, dict):
        return False, "missing_target_status"

    required_flags = (
        "movie_command_ok",
        "ipg_movie_registered",
        "ipg_movie_send_ok",
        "ipg_movie_camera_probe_ok",
        "ipg_movie_view_probe_ok",
    )
    missing = [flag for flag in required_flags if not target_status.get(flag)]
    if missing:
        return False, f"movie_core_failed:{','.join(missing)}"
    return True, "movie_core_ok"


def wait_for_movie_runtime_online_relaxed(
    *,
    timeout_sec: float,
    poll_interval_sec: float,
) -> dict[str, str]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "runtime_verify_movie_runtime_online_probe"
    deadline = time.monotonic() + max(1.0, float(timeout_sec))
    last_detail = "not_ready"

    while time.monotonic() < deadline:
        result = run_check_attempt(
            name=probe_name,
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / f"{probe_name}.txt",
                "IPG-MOVIE",
                [
                    'if {![info exists View(ev.view)]} {error "missing View(ev.view)"}',
                    'set view_key $View(ev.view)',
                    'scan $view_key %d vno',
                    'set wi [dict get $View($view_key) Width]',
                    'set he [dict get $View($view_key) Height]',
                    'set abraxas_menu ".view${vno}.mbar.view.m.show"',
                    'set abraxas_menu_ready [expr {[winfo exists $abraxas_menu] ? 1 : 0}]',
                    'if {[info exists Camera::v(Name)]} {set camera_name $Camera::v(Name)} else {set camera_name ""}',
                    'format "width=%s;height=%s;camera_name=%s;abraxas_menu_ready=%s" $wi $he $camera_name $abraxas_menu_ready',
                ],
            ),
            timeout_sec=min(3.0, max(0.5, deadline - time.monotonic())),
        )
        if result.get("ok"):
            detail = str(result.get("detail") or "").strip()
            payload: dict[str, str] = {}
            for chunk in detail.split(";"):
                key, separator, value = chunk.partition("=")
                if separator:
                    payload[key.strip()] = value.strip()
            width = int(payload.get("width", "0") or "0")
            height = int(payload.get("height", "0") or "0")
            if width > 0 and height > 0 and payload.get("abraxas_menu_ready") == "1":
                payload["mode"] = "runtime_online_relaxed"
                return payload
            last_detail = detail or "runtime_not_ready"
        else:
            last_detail = f"{result.get('kind')}: {result.get('detail')}"

        time.sleep(max(0.1, float(poll_interval_sec)))

    raise RuntimeError(f"Timed out waiting for relaxed IPG-MOVIE runtime readiness: {last_detail}")


async def run_verification(args: argparse.Namespace) -> dict[str, Any]:
    project_root = args.project_root.resolve()
    cm_install = args.cm_install.resolve()
    config_dir = args.config_dir.resolve()
    testrun_rel_path = normalize_testrun_path(project_root, args.testrun)
    vehicle_path, vehicle_key = resolve_vehicle_path(project_root, testrun_rel_path)
    target_sensor, sensor_skip_reason = resolve_target_sensor(vehicle_path, args.camera_sensor)

    summary: dict[str, Any] = {
        "ok": False,
        "project_root": str(project_root),
        "cm_install": str(cm_install),
        "testrun": testrun_rel_path.as_posix(),
        "vehicle_path": str(vehicle_path),
        "vehicle_key": vehicle_key,
        "testrun_control_mode": args.testrun_control_mode,
        "sensor": None if target_sensor is None else str(target_sensor.get("name") or ""),
        "sensor_skip_reason": sensor_skip_reason,
        "steps": {},
    }

    carmaker = None
    movie = None
    carmaker_owned = False
    movie_owned = False

    try:
        if args.clean_existing_processes:
            movie_reset = stop_movie_stack_via_movie_quit(
                timeout_sec=args.movie_quit_timeout_sec,
                probe_name="runtime_verify_movie_quit_before_start",
            )
            summary["steps"]["movie_reset_before_start"] = movie_reset
            print(
                "Movie reset before start: "
                f"mode={movie_reset.get('mode')} gui_before={movie_reset.get('before', {}).get('gui', [])} "
                f"gpu_before={movie_reset.get('before', {}).get('gpu', [])}"
            )

        carmaker, carmaker_pid, carmaker_owned, carmaker_action = await start_or_reuse_carmaker_for_open_movie(
            cm_install,
            args.host,
            project_root,
            args.clean_existing_processes,
        )
        print(f"CarMaker action: {carmaker_action}")
        summary["steps"]["carmaker"] = {
            "action": carmaker_action,
            "pid": carmaker_pid,
        }

        selected_testrun_name = sync_gui_testrun_selection(project_root, testrun_rel_path)
        print(f"CarMaker GUI TestRun selected: {selected_testrun_name}")
        summary["steps"]["testrun_selection"] = {
            "selected": selected_testrun_name,
        }

        await asyncio.sleep(max(0.0, float(args.startup_settle_sec)))

        if args.testrun_control_mode == "tcl":
            _, carmaker_pid, bootstrapped_testrun_name = await bootstrap_testrun_for_movie_via_cmapi(
                project_root=project_root,
                testrun_rel_path=testrun_rel_path,
                variation=None,
                running_timeout_sec=args.bootstrap_running_timeout_sec,
                idle_timeout_sec=args.bootstrap_idle_timeout_sec,
                host=args.host,
                carmaker=carmaker,
                carmaker_pid=carmaker_pid,
            )
            bootstrap_step = {
                "mode": "tcl",
                "label": "StartSim/WaitForStatus/StopSim",
                "testrun": bootstrapped_testrun_name,
            }
        else:
            bootstrap_step = bootstrap_testrun_via_tk_buttons(
                selected_testrun_name,
                running_timeout_sec=args.bootstrap_running_timeout_sec,
                idle_timeout_sec=args.bootstrap_idle_timeout_sec,
            )
        print(f"TestRun bootstrap passed via {args.testrun_control_mode}")
        summary["steps"]["testrun_bootstrap"] = bootstrap_step

        movie, movie_pid, movie_owned, movie_action = await start_or_reuse_movie(
            cm_install,
            args.movie_apphost,
            project_root,
            int(carmaker_pid),
            args.clean_existing_processes,
        )
        print(f"IPG-MOVIE action: {movie_action}")
        summary["steps"]["movie"] = {
            "action": movie_action,
            "pid": movie_pid,
        }

        try:
            movie_scene = wait_for_movie_scene_ready(
                cm_install=cm_install,
                movie_apphost=args.movie_apphost,
                project_root=project_root,
                carmaker_pid=int(carmaker_pid),
                timeout_sec=args.movie_settle_sec,
                poll_interval_sec=args.movie_ready_poll_sec,
                initial_grace_sec=args.movie_ready_grace_sec,
            )
        except RuntimeError as exc:
            if "camera_name=DEFAULT" not in str(exc):
                raise
            movie_scene = wait_for_movie_runtime_online_relaxed(
                timeout_sec=args.movie_settle_sec,
                poll_interval_sec=args.movie_ready_poll_sec,
            )
            movie_scene["strict_scene_ready_fallback"] = str(exc)
        print(
            "IPG-MOVIE scene ready: "
            f"camera={movie_scene.get('camera_name', '<unknown>')} "
            f"size={movie_scene.get('width', '?')}x{movie_scene.get('height', '?')}"
        )
        summary["steps"]["movie_scene_ready"] = movie_scene

        abraxas = ensure_movie_abraxas_enabled(timeout_sec=args.health_check_timeout_sec)
        print(f"IPG-MOVIE ABRAXAS: before={abraxas.get('before')} after={abraxas.get('after')}")
        summary["steps"]["abraxas"] = abraxas

        if target_sensor is not None:
            sensor_name = str(target_sensor.get("name") or "")
            sensor_label = str(target_sensor.get("ipgmovie_sensor_label") or "")
            selected_config_path = config_dir / f"camera.{sensor_name}.json"
            if not selected_config_path.exists():
                raise FileNotFoundError(
                    f"Sensor-specific validation requires config file {selected_config_path} to exist"
                )

            try:
                camera_selection = select_movie_camera_sensor_after_scene_ready(
                    sensor_label,
                    timeout_sec=args.health_check_timeout_sec,
                )
                print(
                    "IPG-MOVIE selected camera sensor after scene ready: "
                    f"requested={camera_selection.get('selected')} current={camera_selection.get('current')} "
                    f"render={camera_selection.get('capture_path')}"
                )
                summary["steps"]["camera_selection"] = camera_selection

                width, height = load_movie_view_size_from_real_image(selected_config_path)
                applied_view = ensure_movie_view_size(width, height, timeout_sec=args.health_check_timeout_sec)
                print(f"IPG-MOVIE view size: {width}x{height}")
                summary["steps"]["view_size"] = {
                    "config_path": str(selected_config_path),
                    "width": width,
                    "height": height,
                    **applied_view,
                }
            except Exception as exc:
                print(f"IPG-MOVIE sensor-specific checks skipped: {exc}")
                summary["steps"]["sensor_runtime"] = {
                    "ok": False,
                    "required": bool(args.require_sensor_runtime),
                    "sensor": sensor_name,
                    "error": str(exc),
                }
                if args.require_sensor_runtime:
                    raise

        camera_widgets = ensure_movie_camera_widgets(timeout_sec=args.health_check_timeout_sec)
        print(
            "IPG-MOVIE camera widgets initialized: "
            f"camera={camera_widgets.get('after_camera')} lens={camera_widgets.get('after_lens')}"
        )
        summary["steps"]["camera_widgets_initialized"] = camera_widgets

        camera_dialogs = ensure_movie_camera_dialogs_normal(timeout_sec=args.health_check_timeout_sec)
        print(
            "IPG-MOVIE dialogs: "
            f"camera_state={camera_dialogs.get('camera_state')} lens_state={camera_dialogs.get('lens_state')}"
        )
        summary["steps"]["camera_dialogs_normal"] = camera_dialogs

        health_summary = run_movie_send_health_check(
            attempts=args.health_check_attempts,
            timeout_sec=args.health_check_timeout_sec,
            settle_sec=args.health_check_settle_sec,
        )
        health_classification = classify_gui_movie_send_health(health_summary)
        health_ok, health_policy_code = evaluate_health_policy(health_classification, args.health_policy)
        print(
            "DDE health check: "
            f"all_ok={health_classification.get('all_ok')} code={health_classification.get('code', 'unknown')} "
            f"policy={args.health_policy} policy_ok={health_ok}"
        )
        summary["steps"]["dde_health_check"] = {
            "summary_path": str(health_summary.get("summary_path") or ""),
            "policy": args.health_policy,
            "policy_ok": health_ok,
            "policy_code": health_policy_code,
            **health_classification,
        }
        if not health_ok:
            raise RuntimeError(
                "Read-only DDE health check failed: "
                f"{health_policy_code} {health_classification.get('message', '')}"
            )

        summary["ok"] = True
        return summary
    finally:
        if not args.keep_movie_open:
            summary.setdefault("steps", {})["movie_reset_on_cleanup"] = stop_movie_stack_via_movie_quit(
                timeout_sec=args.movie_quit_timeout_sec,
                probe_name="runtime_verify_movie_quit_cleanup",
            )
        await cleanup(
            movie,
            carmaker,
            movie_owned=movie_owned,
            carmaker_owned=carmaker_owned,
            keep_movie_open=True,
            keep_carmaker_open=args.keep_carmaker_open,
        )


def main() -> int:
    args = parse_args()
    summary: dict[str, Any]
    try:
        summary = asyncio.run(run_verification(args))
    except Exception as exc:
        summary = {
            "ok": False,
            "error": str(exc),
            "testrun_control_mode": args.testrun_control_mode,
            "testrun": args.testrun,
            "camera_sensor": args.camera_sensor,
        }
        print(f"Runtime verification failed: {exc}")
        if args.print_summary_json:
            emit_summary_json(summary)
        return 1

    print("Runtime verification passed")
    if args.print_summary_json:
        emit_summary_json(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())