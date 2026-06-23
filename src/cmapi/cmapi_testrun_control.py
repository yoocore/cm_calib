from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.wintypes as _wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Optional

import logging
from src.entry.portable_runtime import apply_cmapi_to_current_process

# Pre-parse --cm-install to add cmapi to sys.path before importing
_cm_install_arg: str | None = None
for _i, _arg in enumerate(sys.argv):
    if _arg == "--cm-install" and _i + 1 < len(sys.argv):
        _cm_install_arg = sys.argv[_i + 1]
        break
if _cm_install_arg:
    apply_cmapi_to_current_process(Path(_cm_install_arg))
else:
    apply_cmapi_to_current_process()

import cmapi
from src.health.dde_health_check import classify_health_summary, default_output_dir, render_dde_execute_script, render_result_script, run_check_attempt, run_read_only_health_suite
from scripts.runtime_config_bootstrap import bootstrap_runtime_configs_for_cameras, capture_initial_values_to_config, load_movie_view_size_from_real_image


if not hasattr(cmapi, "InvalidConfigurationException"):
    invalid_configuration_error = getattr(getattr(cmapi, "error", None), "InvalidConfigurationError", None)
    if invalid_configuration_error is not None:
        cmapi.InvalidConfigurationException = invalid_configuration_error


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_CM_INSTALL = Path(os.environ.get("IPGHOME", "D:/IPG")) / "carmaker" / "win64-14.1"
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"
CARMAKER_PROCESS_NAMES = ("CarMaker.win64.exe", "HIL.exe", "CM_Office.exe")
RUNTIME_CARMAKER_PROCESS_NAMES = ("CarMaker.win64.exe", "CM_Office.exe")
DEFAULT_MOVIE_APPHOST = "kel"

logger = logging.getLogger(__name__)
PROCESS_ENUMERATION_COMMAND = r"""
$procs = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('CarMaker.win64.exe', 'HIL.exe', 'CM_Office.exe', 'Movie.exe') } |
    Select-Object ProcessId, Name, CommandLine
if ($null -eq $procs) {
    '[]'
} else {
    @($procs) | ConvertTo-Json -Compress
}
""".strip()
GUI_MOVIE_MARKERS = ("-cmgui", "-apppid", "-cminstance")
GPUSENSOR_MOVIE_MARKERS = ("-mode GPUSensor", "-headless")
SENSOR_NAME_RE = re.compile(r"^(?P<prefix>\s*Sensor\.(?P<index>\d+)\.name\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$")
SENSOR_ACTIVE_RE = re.compile(r"^(?P<prefix>\s*Sensor\.(?P<index>\d+)\.Active\s*=\s*)(?P<value>[01])(?P<suffix>\s*)$")
IPGMOVIE_SENSOR_PREFIX_RE = re.compile(
    r"^CAMERA_RSI-SENSOR\s+Vh(?:cl|ic)\.(?P<name>.+)$",
    re.IGNORECASE,
)
RUNTIME_PROJECTDIR_PROBE_NAME = "cmapi_testrun_control_projectdir_probe"
MOVIE_SCENE_READY_PROBE_NAME = "cmapi_testrun_control_movie_scene_ready_probe"
MOVIE_SEND_HEALTH_CHECK_NAME = "cmapi_testrun_control_movie_send_health"
DEFAULT_MOVIE_SCENE_READY_GRACE_SEC = 45.0
DEFAULT_MOVIE_QUIT_TIMEOUT_SEC = 8.0
CMAPI_CONTROL_SUMMARY_PREFIX = "CMAPI_CONTROL_SUMMARY_JSON:"
TESTRUN_CONTROL_LABEL = "Tcl StartSim/StopSim"
TESTRUN_CONTROL_MODE_LABELS = {
    "tcl": "StartSim/WaitForStatus/StopSim",
    "tk-buttons": ".f.btn.start/.f.btn.stop invoke",
}
BM_CLICK = 0x00F5


class VehicleSensorActivationError(RuntimeError):
    pass


def emit_summary_json(payload: dict[str, Any]) -> None:
    print(CMAPI_CONTROL_SUMMARY_PREFIX, json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _movie_background_tcl_commands(*, include_root: bool = True) -> list[str]:
    commands: list[str] = []
    if include_root:
        commands.extend([
            'catch {wm attributes . -topmost 0}',
        ])
    commands.extend(
        [
            'if {[winfo exists .camera]} {',
            '    catch {wm attributes .camera -topmost 0}',
            '    catch {wm lower .camera}',
            '}',
            'if {[winfo exists .camera.cammoddlg]} {',
            '    catch {wm attributes .camera.cammoddlg -topmost 0}',
            '    catch {wm lower .camera.cammoddlg}',
            '}',
        ]
    )
    return commands


def _run_powershell_json(command: str, timeout_sec: float = 5.0) -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[health] PowerShell command timed out after {timeout_sec:.1f}s "
            "(WMI may be unhealthy); returning empty list"
        )
        return []
    stdout = completed.stdout.strip()
    if not stdout:
        return []
    payload = json.loads(stdout)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    raise RuntimeError(f"Unexpected process enumeration payload: {payload!r}")


# Win32 process enumeration via psapi (no WMI, no taskkill).
# Get-CimInstance Win32_Process and `taskkill /IM` both hang on hosts where
# the WMI service is unhealthy; EnumProcesses + QueryFullProcessImageNameW
# + TerminateProcess complete in single-digit milliseconds.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_PSAPI = ctypes.windll.psapi
_KERNEL32 = ctypes.windll.kernel32


def _win32_find_processes(image_names_lower: set[str]) -> list[dict[str, Any]]:
    """Return list of {"Name": ..., "ProcessId": ...} whose base image name
    matches `image_names_lower` (must be pre-lowercased)."""
    buf = (_wintypes.DWORD * 4096)()
    bytes_ret = _wintypes.DWORD()
    if not _PSAPI.EnumProcesses(buf, ctypes.sizeof(buf), ctypes.byref(bytes_ret)):
        return []
    matches: list[dict[str, Any]] = []
    for pid in buf[: bytes_ret.value // 4]:
        handle = _KERNEL32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            size = _wintypes.DWORD(520)
            name_buf = ctypes.create_unicode_buffer(520)
            ok = _KERNEL32.QueryFullProcessImageNameW(
                handle, 0, name_buf, ctypes.byref(size)
            )
            if not ok:
                continue
            full_path = name_buf.value
            if not full_path:
                continue
            base_name = full_path.rsplit("\\", 1)[-1]
            if base_name.lower() in image_names_lower:
                matches.append({"Name": base_name, "ProcessId": int(pid), "CommandLine": "", "CreationDate": ""})
        finally:
            _KERNEL32.CloseHandle(handle)
    return matches


def _win32_terminate_processes(procs: list[dict[str, Any]]) -> None:
    for proc in procs:
        pid = int(proc["ProcessId"])
        handle = _KERNEL32.OpenProcess(_PROCESS_TERMINATE, False, pid)
        if not handle:
            continue
        try:
            _KERNEL32.TerminateProcess(handle, 1)
        finally:
            _KERNEL32.CloseHandle(handle)


def list_cm_processes() -> list[dict[str, Any]]:
    result = _run_powershell_json(PROCESS_ENUMERATION_COMMAND)
    if not result:
        result = _win32_find_processes({"hil.exe", "movie.exe", "carmaker.win64.exe", "cm_office.exe"})
    return result


def list_carmaker_processes() -> list[dict[str, Any]]:
    return [proc for proc in list_cm_processes() if proc.get("Name") in CARMAKER_PROCESS_NAMES]


def list_runtime_carmaker_processes(processes: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    source = list_carmaker_processes() if processes is None else processes
    return [proc for proc in source if proc.get("Name") in RUNTIME_CARMAKER_PROCESS_NAMES]


def summarize_processes(processes: list[dict[str, Any]]) -> str:
    return ", ".join(f"{proc.get('Name')}[{proc.get('ProcessId')}]" for proc in processes) or "none"


def is_gpusensor_movie_process(process: dict[str, Any]) -> bool:
    command_line = str(process.get("CommandLine") or "")
    command_line_lower = command_line.lower()
    return all(marker.lower() in command_line_lower for marker in GPUSENSOR_MOVIE_MARKERS)


def is_gui_movie_process(process: dict[str, Any]) -> bool:
    if process.get("Name") != "Movie.exe":
        return False
    if is_gpusensor_movie_process(process):
        return False
    command_line = str(process.get("CommandLine") or "")
    if not command_line:
        return True  # psapi fallback: assume GUI Movie when CommandLine unavailable
    command_line_lower = command_line.lower()
    return all(marker.lower() in command_line_lower for marker in GUI_MOVIE_MARKERS)


def list_gui_movie_processes() -> list[dict[str, Any]]:
    return [proc for proc in list_cm_processes() if is_gui_movie_process(proc)]


def list_gpusensor_movie_processes() -> list[dict[str, Any]]:
    return [proc for proc in list_cm_processes() if is_gpusensor_movie_process(proc)]


def snapshot_movie_stack() -> dict[str, list[int]]:
    return {
        "gui": [int(proc["ProcessId"]) for proc in list_gui_movie_processes()],
        "gpu": [int(proc["ProcessId"]) for proc in list_gpusensor_movie_processes()],
    }


def kill_gui_movie_processes() -> list[dict[str, Any]]:
    gui_movies = list_gui_movie_processes()
    if not gui_movies:
        return []

    for proc in gui_movies:
        subprocess.run(
            ["taskkill", "/PID", str(proc["ProcessId"]), "/F", "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
    return gui_movies


def kill_gpusensor_movie_processes() -> list[dict[str, Any]]:
    gpusensor_movies = list_gpusensor_movie_processes()
    if not gpusensor_movies:
        return []

    for proc in gpusensor_movies:
        subprocess.run(
            ["taskkill", "/PID", str(proc["ProcessId"]), "/F", "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
    return gpusensor_movies


def kill_movie_stack_if_gpusensor_present() -> list[dict[str, Any]]:
    gpusensor_movies = list_gpusensor_movie_processes()
    if not gpusensor_movies:
        return []
    stop_movie_stack_via_movie_quit(
        timeout_sec=DEFAULT_MOVIE_QUIT_TIMEOUT_SEC,
        probe_name="cmapi_testrun_control_movie_quit_gpusensor_reset",
    )
    return kill_all_movie_processes() if snapshot_movie_stack()["gui"] or snapshot_movie_stack()["gpu"] else []


def kill_all_movie_processes() -> list[dict[str, Any]]:
    movie_processes = [proc for proc in list_cm_processes() if proc.get("Name") == "Movie.exe"]
    if not movie_processes:
        return []

    for proc in movie_processes:
        subprocess.run(
            ["taskkill", "/PID", str(proc["ProcessId"]), "/F", "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
    return movie_processes


def kill_all_processes() -> list[dict[str, Any]]:
    """Kill ALL CarMaker variants AND Movie processes via Win32 psapi + TerminateProcess.

    Bypasses WMI (Get-CimInstance) and taskkill /IM, both of which hang on
    hosts where the WMI service is unhealthy. EnumProcesses + QueryFull-
    ProcessImageNameW + TerminateProcess complete in single-digit ms.
    """
    target_names_lower = {n.lower() for n in (*CARMAKER_PROCESS_NAMES, "Movie.exe")}
    procs = _win32_find_processes(target_names_lower)
    if not procs:
        return []
    _win32_terminate_processes(procs)
    return procs


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

    killed_movie = kill_all_movie_processes()
    killed_gpu = kill_gpusensor_movie_processes()
    fallback_after = snapshot_movie_stack()
    return {
        "mode": "movie_quit_fallback_taskkill",
        "before": before,
        "after": fallback_after,
        "fallback": True,
        "command_result": command_result,
        "fallback_killed_pids": (
            [int(proc["ProcessId"]) for proc in killed_movie]
            + [int(proc["ProcessId"]) for proc in killed_gpu]
        ),
    }


def kill_existing_cm_processes() -> list[dict[str, Any]]:
    # Reset the whole CarMaker/IPG-MOVIE stack so the next run starts from a
    # known state. Uses Win32 psapi + TerminateProcess to bypass WMI and
    # taskkill /IM, both of which hang when the WMI service is unhealthy.
    target_names_lower = {n.lower() for n in (*CARMAKER_PROCESS_NAMES, "Movie.exe")}
    procs = _win32_find_processes(target_names_lower)
    if not procs:
        return []
    _win32_terminate_processes(procs)
    return procs


def normalize_sensor_name(raw_value: str) -> str:
    value = raw_value.strip()
    match = IPGMOVIE_SENSOR_PREFIX_RE.match(value)
    if match:
        return match.group("name").strip()
    for prefix in ("Vhcl.", "Vhic."):
        if value.lower().startswith(prefix.lower()):
            return value[len(prefix) :].strip()
    return value


def normalize_camera_names(raw_names: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        camera_name = normalize_sensor_name(str(raw_name).strip())
        if not camera_name:
            continue
        key = camera_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(camera_name)
    return names


def collect_vehicle_sensor_state(vehicle_path: Path) -> list[dict[str, Any]]:
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


def load_testrun(project_root: Path, testrun_rel_path: Path) -> cmapi.TestRunParametrization:
    cmapi.Project.load(project_root.resolve())
    project = cmapi.Project.instance()
    return project.load_testrun_parametrization(testrun_rel_path)


def resolve_vehicle_path(project_root: Path, testrun_rel_path: Path) -> tuple[Path, str]:
    testrun = load_testrun(project_root, testrun_rel_path)
    vehicle_key = str(testrun.get_parameter_value("Vehicle")).strip()
    if not vehicle_key:
        raise ValueError(f"TestRun {testrun_rel_path.as_posix()} does not define Vehicle")
    vehicle_path = project_root / "Data" / "Vehicle" / Path(vehicle_key.replace("\\", "/"))
    return require_file(vehicle_path, "Vehicle file"), vehicle_key


def activate_single_vehicle_sensor(vehicle_path: Path, requested_sensor: str) -> dict[str, Any]:
    target_name = normalize_sensor_name(requested_sensor)
    text = vehicle_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    sensor_names: dict[str, str] = {}
    sensor_name_by_index: dict[str, str] = {}
    active_line_indexes: dict[str, int] = {}

    for line_index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        name_match = SENSOR_NAME_RE.match(stripped)
        if name_match:
            sensor_index = name_match.group("index")
            sensor_name = name_match.group("value").strip()
            sensor_name_by_index[sensor_index] = sensor_name
            sensor_names[sensor_name.casefold()] = sensor_index
            continue

        active_match = SENSOR_ACTIVE_RE.match(stripped)
        if active_match:
            active_line_indexes[active_match.group("index")] = line_index

    target_index = sensor_names.get(target_name.casefold())
    if target_index is None:
        available = ", ".join(sensor_name_by_index[index] for index in sorted(sensor_name_by_index, key=int))
        raise VehicleSensorActivationError(
            f"Sensor {requested_sensor!r} was not found in {vehicle_path.name}. Available sensors: {available}"
        )

    missing_active = [
        sensor_name_by_index[index]
        for index in sorted(sensor_name_by_index, key=int)
        if index not in active_line_indexes
    ]
    if missing_active:
        raise VehicleSensorActivationError(
            f"Vehicle file is missing Sensor.Active entries for: {', '.join(missing_active)}"
        )

    changed = False
    for sensor_index, sensor_name in sensor_name_by_index.items():
        active_line_index = active_line_indexes[sensor_index]
        existing_line = lines[active_line_index].rstrip("\r\n")
        active_match = SENSOR_ACTIVE_RE.match(existing_line)
        if active_match is None:
            raise VehicleSensorActivationError(
                f"Failed to parse Sensor.Active line for index {sensor_index}: {existing_line!r}"
            )
        desired_value = "1" if sensor_index == target_index else "0"
        new_line = (
            f"{active_match.group('prefix')}{desired_value}{active_match.group('suffix')}"
            f"{lines[active_line_index][len(existing_line):]}"
        )
        if new_line != lines[active_line_index]:
            lines[active_line_index] = new_line
            changed = True

    if changed:
        vehicle_path.write_text("".join(lines), encoding="utf-8")

    return {
        "vehicle_path": str(vehicle_path),
        "selected_sensor_name": sensor_name_by_index[target_index],
        "selected_sensor_index": int(target_index),
        "ipgmovie_sensor_label": f"CAMERA_RSI-SENSOR Vhcl.{sensor_name_by_index[target_index]}",
        "changed": changed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use CarMaker CMAPI to start CarMaker, load a TestRun, run or stop the "
            "simulation, and optionally open IPG-MOVIE."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("run", "status", "prepare"),
        default="run",
        help="Execution mode: run keeps legacy behavior, status emits a read-only JSON snapshot, prepare emits a prepare-runtime JSON snapshot.",
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
            "Vehicle sensor name to activate before the run. Accepts either the plain "
            "Sensor.xx.name value or the IPG-MOVIE label CAMERA_RSI-SENSOR Vhcl.<name>."
        ),
    )
    parser.add_argument(
        "--prepare-camera",
        action="append",
        dest="prepare_cameras",
        default=[],
        help="Deprecated. Prepare no longer generates camera configs; use the precheck/config-generation step instead.",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help="Directory containing generated camera.<name>.json runtime configs.",
    )
    parser.add_argument(
        "--bootstrap-template",
        type=Path,
        default=None,
        help="Optional bootstrap template path. Defaults to configs/bootstrap.template.json next to the script.",
    )
    parser.add_argument(
        "--movie-dir",
        type=Path,
        default=None,
        help="Optional Movie directory used to locate raw and annotated bootstrap images.",
    )
    parser.add_argument(
        "--clean-existing-processes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "If CarMaker reuse validation fails or multiple CarMaker instances are detected, "
            "kill existing CarMaker.win64.exe and Movie.exe processes before starting a fresh run."
        ),
    )
    parser.add_argument(
        "--open-movie",
        action="store_true",
        help="Start IPG-MOVIE and attach it to the started CarMaker process.",
    )
    parser.add_argument(
        "--stop-after",
        type=float,
        default=None,
        help=(
            "If set without --open-movie, stop the simulation after the given number of seconds. "
            "When --open-movie is enabled, the script follows the manual bootstrap flow instead."
        ),
    )
    parser.add_argument(
        "--startup-settle-sec",
        type=float,
        default=2.0,
        help="Seconds to wait after CarMaker startup before attaching clients.",
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
        help="Polling interval used while waiting for the IPG-MOVIE calibration scene to become ready.",
    )
    parser.add_argument(
        "--movie-ready-grace-sec",
        type=float,
        default=DEFAULT_MOVIE_SCENE_READY_GRACE_SEC,
        help=(
            "Initial grace window before scene-ready send probe failures can trigger GUI Movie recovery "
            "or fallback classification."
        ),
    )
    parser.add_argument(
        "--bootstrap-running-timeout-sec",
        type=float,
        default=60.0,
        help="Maximum seconds to wait for the bootstrap TestRun to reach running before opening IPG-MOVIE.",
    )
    parser.add_argument(
        "--bootstrap-idle-timeout-sec",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for the bootstrap TestRun to return to idle after stop is requested.",
    )
    parser.add_argument(
        "--testrun-control-mode",
        choices=("tcl", "tk-buttons"),
        default="tcl",
        help="Bootstrap TestRun via pure Tcl StartSim/StopSim or via CarMaker Tk button invoke semantics.",
    )
    parser.add_argument(
        "--apo-connect-retries",
        type=int,
        default=20,
        help="Maximum number of retries when connecting SimControlInteractive.",
    )
    parser.add_argument(
        "--apo-connect-delay-sec",
        type=float,
        default=0.5,
        help="Delay between APO connection retries.",
    )
    parser.add_argument(
        "--keep-carmaker-open",
        action="store_true",
        help="Do not stop CarMaker during cleanup.",
    )
    parser.add_argument(
        "--keep-movie-open",
        action="store_true",
        help="Do not stop IPG-MOVIE during cleanup.",
    )
    parser.add_argument(
        "--health-check-after-start",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run a read-only IPG-MOVIE remote-control health check after the startup chain finishes.",
    )
    parser.add_argument(
        "--health-check-attempts",
        type=int,
        default=2,
        help="Attempts per check when running the post-start Movie remote-control health check.",
    )
    parser.add_argument(
        "--health-check-timeout-sec",
        type=float,
        default=2.5,
        help="Timeout per check attempt for the post-start Movie remote-control health check.",
    )
    parser.add_argument(
        "--health-check-settle-sec",
        type=float,
        default=0.3,
        help="Retry delay between attempts for the post-start Movie remote-control health check.",
    )
    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="Emit a machine-readable JSON summary line prefixed with CMAPI_CONTROL_SUMMARY_JSON:.",
    )
    return parser.parse_args()


def build_status_summary(
    *,
    project_root: Path,
    cm_install: Path,
    testrun_rel_path: Path,
    vehicle_path: Path,
    vehicle_key: str,
    camera_sensor: Optional[str],
    health_check_after_start: bool,
    health_check_attempts: int,
    health_check_timeout_sec: float,
    health_check_settle_sec: float,
) -> dict[str, Any]:
    processes = list_cm_processes()
    carmakers = list_carmaker_processes()
    runtime_carmakers = list_runtime_carmaker_processes(carmakers)
    gui_carmakers = [proc for proc in carmakers if proc.get("Name") == "HIL.exe"]
    gui_movies = list_gui_movie_processes()
    gpusensor_movies = list_gpusensor_movie_processes()
    running_projectdir = probe_running_carmaker_projectdir()
    sensors = collect_vehicle_sensor_state(vehicle_path)
    active_sensors = [sensor["name"] for sensor in sensors if sensor.get("active")]
    health: Optional[dict[str, Any]] = None
    if health_check_after_start:
        output_dir = default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        health_summary = run_read_only_health_suite(
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            attempts=max(1, int(health_check_attempts)),
            timeout_sec=max(0.1, float(health_check_timeout_sec)),
            settle_sec=max(0.0, float(health_check_settle_sec)),
        )
        classification = health_summary.get("classification") if isinstance(health_summary, dict) else None
        if isinstance(classification, dict):
            health = classification

    status_issues: list[str] = []
    if running_projectdir is None:
        status_issues.append("CarMaker projectdir is not readable")
    elif running_projectdir.resolve() != project_root.resolve():
        status_issues.append(
            f"CarMaker projectdir mismatch: expected {project_root.as_posix()}, got {running_projectdir.as_posix()}"
        )
    if len(runtime_carmakers) != 1:
        status_issues.append(f"expected exactly 1 CarMaker backend runtime, found {len(runtime_carmakers)}")
    if len(gui_carmakers) < 1:
        status_issues.append("CarMaker GUI (HIL.exe) is not running")
    if len(gui_movies) < 1:
        status_issues.append("GUI Movie is not running")
    if not active_sensors:
        status_issues.append("no active camera sensor in Vehicle")
    if health is not None and str(health.get("code") or "") != "ok":
        status_issues.append(str(health.get("message") or health.get("code") or "Movie remote-control health check failed"))

    status = "ready" if not status_issues else "passive"
    status_reason = "runtime ready" if not status_issues else "; ".join(status_issues)

    return {
        "mode": "status",
        "project_root": str(project_root),
        "cm_install": str(cm_install),
        "testrun": testrun_rel_path.as_posix(),
        "testrun_control": TESTRUN_CONTROL_LABEL,
        "vehicle": vehicle_key,
        "vehicle_path": str(vehicle_path),
        "camera_sensor_requested": camera_sensor,
        "running_projectdir": str(running_projectdir) if running_projectdir else None,
        "processes": processes,
        "process_counts": {
            "carmaker": len(carmakers),
            "carmaker_runtime": len(runtime_carmakers),
            "carmaker_gui": len(gui_carmakers),
            "gui_movie": len(gui_movies),
            "gpusensor_movie": len(gpusensor_movies),
        },
        "sensors": sensors,
        "active_sensors": active_sensors,
        "health": health,
        "status": status,
        "status_reason": status_reason,
    }


def check_movie_fbo(*, timeout_sec: float = 5.0) -> None:
    """Probe IPG-MOVIE FBO health by creating a tiny test FBO.
    Raises RuntimeError if FBO is corrupted or DDE fails.
    """
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_testrun_control_fbo_probe"
    result = run_check_attempt(
        name=probe_name,
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        script_text=render_dde_execute_script(
            output_dir / f"{probe_name}.txt",
            "IPG-MOVIE",
            [
                r"set orig_state [wm state .]",
                r"if {$orig_state ne {iconic}} {",
                r"    wm state . iconic",
                r"    after 100",
                r"}",
                r"set fbo_rc [catch {",
                r"    set fbo [FBO new 16 16 -tex rgb -noclear]",
                r"    FBO begin $fbo",
                r"    FBO end",
                r"    FBO delete $fbo",
                r"} fbo_msg]",
                r"if {$orig_state ne {iconic}} {",
                r"    wm state . $orig_state",
                r"}",
                r"# After restoring window, push it behind active windows",
                r"catch {wm attributes . -topmost 0}",
                r"catch {wm lower .}",

                r"if {$fbo_rc != 0} {",
                r'    error "FBO probe failed: $fbo_msg"',
                r"}",
                r"list ok {FBO probe passed}",
            ],
        ),
        timeout_sec=timeout_sec,
    )
    if not result.get("ok"):
        raise RuntimeError(
            f"IPG-MOVIE FBO corruption detected: {result.get('kind')}: {result.get('detail')}"
        )


async def execute_prepare_mode(args: argparse.Namespace, *, _fbo_retry: bool = False) -> dict[str, Any]:
    project_root = args.project_root.resolve()
    cm_install = args.cm_install.resolve()
    config_dir = args.config_dir.resolve()
    testrun_rel_path = normalize_testrun_path(project_root, args.testrun)
    vehicle_path, vehicle_key = resolve_vehicle_path(project_root, testrun_rel_path)
    variation = load_variation(project_root, testrun_rel_path)

    config_bootstrap: list[dict[str, Any]] = []
    config_bootstrap_warning: Optional[str] = None
    if args.prepare_cameras:
        config_bootstrap_warning = (
            "prepare no longer generates camera configs; ignored --prepare-camera and expected configs to exist already"
        )

    sensor_activation_result: Optional[dict[str, Any]] = None
    selected_config_path: Optional[Path] = None
    if args.camera_sensor:
        sensor_activation_result = activate_single_vehicle_sensor(vehicle_path, args.camera_sensor)
        selected_config_path = (config_dir / f"camera.{sensor_activation_result['selected_sensor_name']}.json").resolve()
        if not selected_config_path.exists():
            raise FileNotFoundError(
                f"Prepare requires an existing config file before runtime setup: {selected_config_path}"
            )

    carmaker, carmaker_pid, carmaker_owned, carmaker_action = await start_or_reuse_carmaker_for_open_movie(
        cm_install,
        args.host,
        project_root,
        args.clean_existing_processes,
    )
    movie: Optional[cmapi.IPGMovie] = None
    movie_pid: Optional[int] = None
    movie_owned = False
    health_summary: Optional[dict[str, Any]] = None
    movie_scene: Optional[dict[str, str]] = None
    selected_testrun_name: Optional[str] = None
    bootstrapped_testrun_name: Optional[str] = None
    bootstrap_step: Optional[dict[str, Any]] = None
    abraxas: Optional[dict[str, str]] = None
    camera_selection: Optional[dict[str, str]] = None
    view_size: Optional[dict[str, str]] = None
    camera_dialogs: Optional[dict[str, str]] = None
    initial_capture: Optional[dict[str, Any]] = None

    try:
        selected_testrun_name = sync_gui_testrun_selection(project_root, testrun_rel_path)
        if args.testrun_control_mode == "tcl":
            carmaker, carmaker_pid, bootstrapped_testrun_name = await bootstrap_testrun_for_movie_via_cmapi(
                project_root=project_root,
                testrun_rel_path=testrun_rel_path,
                variation=variation,
                running_timeout_sec=args.bootstrap_running_timeout_sec,
                idle_timeout_sec=args.bootstrap_idle_timeout_sec,
                apo_connect_retries=args.apo_connect_retries,
                apo_connect_delay_sec=args.apo_connect_delay_sec,
                host=args.host,
                carmaker=carmaker,
                carmaker_pid=carmaker_pid,
            )
            bootstrap_step = {
                "mode": "tcl",
                "label": TESTRUN_CONTROL_MODE_LABELS["tcl"],
                "testrun": bootstrapped_testrun_name,
            }
        else:
            bootstrap_step = bootstrap_testrun_via_tk_buttons(
                selected_testrun_name,
                running_timeout_sec=args.bootstrap_running_timeout_sec,
                idle_timeout_sec=args.bootstrap_idle_timeout_sec,
            )
            bootstrapped_testrun_name = selected_testrun_name
        movie, movie_pid, movie_owned, movie_action = await start_or_reuse_movie(
            cm_install,
            args.movie_apphost,
            project_root,
            carmaker_pid,
            args.clean_existing_processes,
        )
        try:
            movie_scene = wait_for_movie_scene_ready(
                cm_install=cm_install,
                movie_apphost=args.movie_apphost,
                project_root=project_root,
                carmaker_pid=carmaker_pid,
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
        # --- FBO health check: detect corrupted GL context from stale processes ---
        try:
            check_movie_fbo(timeout_sec=args.health_check_timeout_sec)
            print("IPG-MOVIE FBO health check passed")
        except RuntimeError as fbo_exc:
            if not _fbo_retry:
                killed = kill_all_processes()
                print(f"IPG-MOVIE FBO corruption detected: {fbo_exc}")
                print(f"Killed {len(killed)} stale processes (CarMaker+Movie), retrying prepare with clean state...")
                time.sleep(3)
                return await execute_prepare_mode(args, _fbo_retry=True)
            raise
        abraxas = ensure_movie_abraxas_enabled(timeout_sec=args.health_check_timeout_sec)
        if sensor_activation_result is not None:
            camera_selection = ensure_movie_camera_selected(
                sensor_activation_result["ipgmovie_sensor_label"],
                timeout_sec=args.health_check_timeout_sec,
            )
            movie_scene["camera_name"] = str(camera_selection.get("current") or movie_scene.get("camera_name") or "")
            width, height = load_movie_view_size_from_real_image(selected_config_path)
            view_size = ensure_movie_view_size(width, height, timeout_sec=args.health_check_timeout_sec)
            movie_scene["width"] = str(width)
            movie_scene["height"] = str(height)
        camera_widgets = ensure_movie_camera_widgets(timeout_sec=args.health_check_timeout_sec)
        camera_dialogs = ensure_movie_camera_dialogs_normal(timeout_sec=args.health_check_timeout_sec)
        if sensor_activation_result is not None:
            initial_capture = capture_initial_values_to_config(selected_config_path)
        if args.health_check_after_start:
            health_summary = run_movie_send_health_check(
                attempts=args.health_check_attempts,
                timeout_sec=args.health_check_timeout_sec,
                settle_sec=args.health_check_settle_sec,
            )
        sensors = collect_vehicle_sensor_state(vehicle_path)
        return {
            "mode": "prepare",
            "project_root": str(project_root),
            "cm_install": str(cm_install),
            "testrun": testrun_rel_path.as_posix(),
            "testrun_control": TESTRUN_CONTROL_MODE_LABELS[args.testrun_control_mode],
            "testrun_control_mode": args.testrun_control_mode,
            "selected_testrun": selected_testrun_name,
            "bootstrapped_testrun": bootstrapped_testrun_name,
            "testrun_bootstrap": bootstrap_step,
            "vehicle": vehicle_key,
            "vehicle_path": str(vehicle_path),
            "sensor_activation": sensor_activation_result,
            "config_bootstrap": config_bootstrap,
            "config_bootstrap_warning": config_bootstrap_warning,
            "carmaker": {
                "pid": carmaker_pid,
                "owned": carmaker_owned,
                "action": carmaker_action,
            },
            "movie": {
                "pid": movie_pid,
                "owned": movie_owned,
                "action": movie_action,
                "scene": movie_scene,
                "abraxas": abraxas,
                "view_size": view_size,
                "camera_selection": camera_selection,
                "camera_widgets": camera_widgets,
                "camera_dialogs": camera_dialogs,
            },
            "config_initial_capture": initial_capture,
            "health": classify_gui_movie_send_health(health_summary) if health_summary else None,
            "sensors": sensors,
            "active_sensors": [sensor["name"] for sensor in sensors if sensor.get("active")],
            "status": "ready",
        }
    finally:
        await cleanup(
            movie=movie,
            carmaker=carmaker,
            movie_owned=movie_owned,
            carmaker_owned=carmaker_owned,
            keep_movie_open=True,
            keep_carmaker_open=True,
        )


def normalize_testrun_path(project_root: Path, raw_testrun: str) -> Path:
    testrun_path = Path(raw_testrun.replace("\\", "/"))
    data_testrun_root = project_root / "Data" / "TestRun"

    if testrun_path.is_absolute():
        resolved = testrun_path.resolve()
        try:
            return resolved.relative_to(data_testrun_root.resolve())
        except ValueError as exc:
            raise ValueError(
                f"Absolute TestRun path must be inside {data_testrun_root}"
            ) from exc

    parts = list(testrun_path.parts)
    if len(parts) >= 2 and parts[0].lower() == "data" and parts[1].lower() == "testrun":
        testrun_path = Path(*parts[2:])

    resolved_candidate = data_testrun_root / testrun_path
    if not resolved_candidate.exists():
        raise FileNotFoundError(
            f"TestRun not found: {resolved_candidate}"
        )
    return testrun_path


def require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def probe_running_carmaker_projectdir(timeout_sec: float = 2.0) -> Optional[Path]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_check_attempt(
        name=RUNTIME_PROJECTDIR_PROBE_NAME,
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        script_text=render_result_script(
            output_dir / f"{RUNTIME_PROJECTDIR_PROBE_NAME}.txt",
            ["set projectdir [pwd]"],
        ),
        timeout_sec=timeout_sec,
    )
    if not result.get("ok"):
        return None

    projectdir = str(result.get("detail") or "").strip()
    if not projectdir:
        return None
    return Path(projectdir).resolve()


def load_variation(project_root: Path, testrun_rel_path: Path) -> cmapi.Variation:
    testrun = load_testrun(project_root, testrun_rel_path)
    return cmapi.Variation.create_from_testrun(testrun)


def sync_gui_testrun_selection(
    project_root: Path,
    testrun_rel_path: Path,
    timeout_sec: float = 20.0,
) -> str:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_testrun_control_load_testrun_probe"
    expected_name = testrun_rel_path.name
    escaped_testrun = testrun_rel_path.as_posix().replace("\\", "/").replace('"', '\\"')
    result = run_check_attempt(
        name=probe_name,
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        script_text=render_result_script(
            output_dir / f"{probe_name}.txt",
            [
                'if {[info exists TestRun(FName)]} {set selected_testrun $TestRun(FName)} else {set selected_testrun ""}',
                f'if {{$selected_testrun ne "{expected_name}"}} {{LoadTestRun "{escaped_testrun}"; set selected_testrun $TestRun(FName)}}',
                "set selected_testrun $TestRun(FName)",
            ],
        ),
        timeout_sec=timeout_sec,
    )
    if not result.get("ok"):
        raise RuntimeError(f"Failed to sync CarMaker GUI TestRun selection: {result.get('kind')} {result.get('detail')}")

    selected_name = str(result.get("detail") or "").strip()
    if selected_name != expected_name:
        raise RuntimeError(
            "CarMaker GUI TestRun selection did not match requested TestRun: "
            f"expected {expected_name}, got {selected_name or '<empty>'}"
        )
    return selected_name


def _send_window_message(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> int:
    return int(ctypes.windll.user32.SendMessageW(int(hwnd), msg, wparam, lparam))


def wait_for_carmaker_test_window(
    expected_testrun_name: str,
    timeout_sec: float = 15.0,
    poll_interval_sec: float = 0.2,
):
    from pywinauto import Desktop

    deadline = time.monotonic() + timeout_sec
    title_prefix = f"CarMaker Office - Test: {expected_testrun_name}"
    last_titles: list[str] = []
    while time.monotonic() < deadline:
        try:
            windows = Desktop(backend="win32").windows()
        except Exception as exc:
            raise RuntimeError(f"Failed to enumerate CarMaker test window: {exc}") from exc

        candidates = []
        visible_titles: list[str] = []
        for window in windows:
            title = str(window.window_text() or "").strip()
            if not title:
                continue
            if title.startswith("CarMaker Office - Test:"):
                visible_titles.append(title)
            if title.startswith(title_prefix):
                candidates.append(window)

        if candidates:
            return candidates[0]

        last_titles = visible_titles
        time.sleep(max(0.05, poll_interval_sec))

    raise RuntimeError(
        "Timed out waiting for CarMaker test window: "
        f"expected prefix={title_prefix!r}, visible={last_titles or ['<none>']}"
    )


def resolve_carmaker_test_window_buttons(window) -> tuple[tuple[int, tuple[int, int, int, int]], tuple[int, tuple[int, int, int, int]]]:
    window_rect = window.rectangle()
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    for ctrl in window.descendants():
        try:
            if getattr(ctrl.element_info, "class_name", "") != "Button":
                continue
            rect = ctrl.rectangle()
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 50 or width > 90 or height < 28 or height > 45:
                continue
            if rect.left < window_rect.right - 120:
                continue
            candidates.append(
                (
                    int(getattr(ctrl, "handle", 0)),
                    (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
                )
            )
        except Exception:
            continue

    candidates.sort(key=lambda item: item[1][1])
    if len(candidates) < 2:
        raise RuntimeError(f"Unable to resolve CarMaker Start/Stop buttons; found {len(candidates)} candidates")
    return candidates[0], candidates[1]


def wait_for_carmaker_status(status: str, timeout_ms: int, *, probe_name: str) -> dict[str, Any]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_check_attempt(
        name=probe_name,
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        script_text=render_result_script(
            output_dir / f"{probe_name}.txt",
            [
                f"WaitForStatus {status} {int(timeout_ms)}",
                f'format "status=%s" "{status}"',
            ],
        ),
        timeout_sec=max(1.0, float(timeout_ms) / 1000.0 + 5.0),
    )
    if not result.get("ok"):
        raise RuntimeError(f"Failed waiting for CarMaker status {status}: {result.get('kind')}: {result.get('detail')}")
    return _parse_probe_detail(str(result.get("detail") or "").strip())


def bootstrap_testrun_via_tk_buttons(
    expected_testrun_name: str,
    *,
    running_timeout_sec: float,
    idle_timeout_sec: float,
) -> dict[str, Any]:
    running_timeout_ms = max(1000, int(max(1.0, float(running_timeout_sec)) * 1000))
    idle_timeout_ms = max(1000, int(max(1.0, float(idle_timeout_sec)) * 1000))

    wait_for_carmaker_status("idle", 10000, probe_name="cmapi_prepare_tk_buttons_idle_before")
    start_invoke = run_tcl_sim_command(
        commands=[
            'if {![winfo exists .f.btn.start]} {error "missing widget .f.btn.start"}',
            ".f.btn.start invoke",
            "update",
            "update idletasks",
        ],
        probe_name="cmapi_prepare_tk_buttons_start_invoke",
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
        probe_name="cmapi_prepare_tk_buttons_running",
    )

    stop_invoke = run_tcl_sim_command(
        commands=[
            'if {![winfo exists .f.btn.stop]} {error "missing widget .f.btn.stop"}',
            ".f.btn.stop invoke",
            "update",
            "update idletasks",
        ],
        probe_name="cmapi_prepare_tk_buttons_stop_invoke",
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
        probe_name="cmapi_prepare_tk_buttons_idle",
    )

    return {
        "mode": "tk-buttons",
        "label": TESTRUN_CONTROL_MODE_LABELS["tk-buttons"],
        "testrun": expected_testrun_name,
        "start_invoke": start_invoke,
        "stop_invoke": stop_invoke,
        "running": running,
        "idle": idle,
    }


def click_carmaker_test_button(
    button_handle: int,
    button_rect: tuple[int, int, int, int],
) -> None:
    _send_window_message(button_handle, BM_CLICK, 0, 0)


async def bootstrap_testrun_for_movie_via_cmapi(
    *,
    project_root: Path,
    testrun_rel_path: Path,
    variation: cmapi.Variation,
    running_timeout_sec: float,
    idle_timeout_sec: float,
    apo_connect_retries: int = 20,
    apo_connect_delay_sec: float = 0.5,
    host: str = "localhost",
    carmaker: Optional[cmapi.CarMaker] = None,
    carmaker_pid: Optional[int] = None,
) -> tuple[cmapi.CarMaker, int, str]:
    expected_name = testrun_rel_path.name
    resolved_pid = carmaker_pid
    resolved_carmaker = carmaker
    if resolved_pid is None:
        resolved_pid = wait_for_runtime_carmaker_pid(project_root, timeout_sec=20.0, poll_interval_sec=0.25)
    if resolved_carmaker is None:
        resolved_carmaker = attach_to_existing_carmaker(resolved_pid, host, project_root)

    wait_for_carmaker_tcleval_ready()

    gpusensor_movies = list_gpusensor_movie_processes()
    if not gpusensor_movies:
        stop_movie_stack_via_movie_quit(
            timeout_sec=DEFAULT_MOVIE_QUIT_TIMEOUT_SEC,
            probe_name="cmapi_testrun_control_movie_quit_no_gpusensor",
        )

    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_testrun_control_bootstrap_probe"
    running_timeout_ms = max(1000, int(max(1.0, float(running_timeout_sec)) * 1000))
    idle_timeout_ms = max(1000, int(max(1.0, float(idle_timeout_sec)) * 1000))
    wait_for_carmaker_status("idle", 10000, probe_name=f"{probe_name}_idle_before")

    try:
        start_simulation_via_tcl(
            running_timeout_sec=max(1.0, float(running_timeout_ms) / 1000.0),
            probe_name=f"{probe_name}_start",
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to start TestRun via {TESTRUN_CONTROL_LABEL}: {exc}") from exc

    try:
        stop_simulation_via_tcl(
            idle_timeout_sec=max(1.0, float(idle_timeout_ms) / 1000.0),
            probe_name=f"{probe_name}_stop",
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to stop TestRun via {TESTRUN_CONTROL_LABEL}: {exc}") from exc

    selected_name = sync_gui_testrun_selection(project_root, testrun_rel_path)

    return resolved_carmaker, resolved_pid, selected_name


def bootstrap_testrun_for_movie_via_cmapi_sync(
    *,
    project_root: Path,
    testrun_rel_path: Path,
    running_timeout_sec: float,
    idle_timeout_sec: float,
    apo_connect_retries: int = 20,
    apo_connect_delay_sec: float = 0.5,
    host: str = "localhost",
    carmaker_pid: Optional[int] = None,
) -> tuple[int, str]:
    variation = load_variation(project_root, testrun_rel_path)
    _, resolved_pid, selected_name = asyncio.run(
        bootstrap_testrun_for_movie_via_cmapi(
            project_root=project_root,
            testrun_rel_path=testrun_rel_path,
            variation=variation,
            running_timeout_sec=running_timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            apo_connect_retries=apo_connect_retries,
            apo_connect_delay_sec=apo_connect_delay_sec,
            host=host,
            carmaker_pid=carmaker_pid,
        )
    )
    return resolved_pid, selected_name


def resolve_carmaker_executable(cm_install: Path) -> Path:
    preferred_hil = cm_install / "GUI" / "HIL.exe"
    if preferred_hil.exists():
        return preferred_hil
    return require_file(cm_install / "bin" / "CarMaker.win64.exe", "CarMaker executable")


def wait_for_carmaker_tcleval_ready(
    timeout_sec: float = 30.0,
    poll_interval_sec: float = 0.5,
) -> None:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_testrun_control_tcleval_ready_probe"
    deadline = time.monotonic() + timeout_sec
    last_detail = "not_ready"
    while time.monotonic() < deadline:
        result = run_check_attempt(
            name=probe_name,
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_result_script(
                output_dir / f"{probe_name}.txt",
                ["set ready ok"],
            ),
            timeout_sec=min(2.0, max(0.5, deadline - time.monotonic())),
        )
        if result.get("ok") and str(result.get("detail") or "").strip() == "ok":
            return
        last_detail = f"{result.get('kind')}: {result.get('detail')}"
        time.sleep(poll_interval_sec)
    raise RuntimeError(f"Timed out waiting for CarMaker TclEval readiness: {last_detail}")


def cancel_movie_updateview_timer(*, timeout_sec: float = 10.0) -> None:
    """Send 'after cancel UpdateView_TimerProc' to IPG-MOVIE to prevent
    CheckViewPort recursion when the 30s internal timer fires after
    bootstrap (StartSim/StopSim).

    Non-fatal on failure: timer may have already fired, or IPG-MOVIE may
    not be ready for DDE communication yet. The capture body also has its
    own 'after cancel UpdateView_TimerProc' as a fallback.
    """
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_check_attempt(
            name="cancel_movie_updateview_timer",
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / "cancel_movie_updateview_timer.txt",
                "IPG-MOVIE",
                ["after cancel UpdateView_TimerProc"],
            ),
            timeout_sec=timeout_sec,
        )
        if not result.get("ok"):
            print(f"[INFO] could not cancel Movie UpdateView timer (non-fatal): {result.get('detail')}")
        else:
            print("Canceled Movie UpdateView_TimerProc before timer fire")
    except Exception as exc:
        print(f"[INFO] cancel Movie UpdateView timer failed (non-fatal): {exc}")


def disable_movie_updateview_timer(*, timeout_sec: float = 10.0) -> None:
    """Rename UpdateView_TimerProc to no-op for the entire camera switch cycle.
    Unlike cancel_movie_updateview_timer() which uses 'after cancel' (only cancels
    ONE timer instance), this renames the proc to a no-op so any remaining or
    newly registered timers are harmless. Restore with enable_movie_updateview_timer().
    Non-fatal on failure.
    """
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_check_attempt(
            name="disable_movie_updateview",
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / "disable_movie_updateview.txt",
                "IPG-MOVIE",
                [
                    "catch {after cancel UpdateView_TimerProc}",
                    "catch {rename UpdateView_TimerProc __saved_UpdateView_TimerProc_orch}",
                    "proc UpdateView_TimerProc {args} {}",
                    "catch {puts stdout \"DIAG_CARMAKER_ERR: [set ::errorInfo]\"}",
                ],
            ),
            timeout_sec=timeout_sec,
        )
        if not result.get("ok"):
            print(f"[INFO] could not disable Movie UpdateView timer (non-fatal): {result.get('detail')}")
        else:
            print("Disabled Movie UpdateView_TimerProc (rename + no-op)")
    except Exception as exc:
        print(f"[INFO] disable Movie UpdateView timer failed (non-fatal): {exc}")


def enable_movie_updateview_timer(*, timeout_sec: float = 10.0) -> None:
    """Restore the original UpdateView_TimerProc after disable_movie_updateview_timer().
    Renames the no-op back and re-schedules the rendering loop timer.
    Non-fatal on failure.
    """
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_check_attempt(
            name="enable_movie_updateview",
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / "enable_movie_updateview.txt",
                "IPG-MOVIE",
                [
                    "if {[info commands __saved_UpdateView_TimerProc_orch] ne \"\"} {",
                    "    catch {rename UpdateView_TimerProc {}}",
                    "    catch {rename __saved_UpdateView_TimerProc_orch UpdateView_TimerProc}",
                    "    catch {after 0 UpdateView_TimerProc}",
                    "}",
                ],
            ),
            timeout_sec=timeout_sec,
        )
        if not result.get("ok"):
            print(f"[INFO] could not enable Movie UpdateView timer (non-fatal): {result.get('detail')}")
        else:
            print("Enabled Movie UpdateView_TimerProc (restored + after 0)")
    except Exception as exc:
        print(f"[INFO] enable Movie UpdateView timer failed (non-fatal): {exc}")


def wrap_checkviewport(*, timeout_sec: float = 10.0) -> None:
    """Install a re-entrant guard on IPG-MOVIE's CheckViewPort Tcl proc and
    a delete-trace to auto-reinstall the guard whenever IPG-MOVIE
    re-registers the proc via Tcl_Eval.

    The guard renames the original CheckViewPort to CheckViewPort_saved and
    installs a wrapper that uses a per-widget re-entrant flag to prevent
    infinite recursion. The 'trace add command CheckViewPort delete' fires
    on proc re-registration and schedules an 'after 0 ::ReGuardCheckViewPort'
    to re-wrap the new CheckViewPort before any pending timer fires.

    Idempotent: checks body content for a marker string to detect already-guarded state.
    Non-fatal on failure.
    """
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        body_lines = [
            '# --- Define re-guard proc (idempotent, redefinition is safe) ---',
            'proc ::ReGuardCheckViewPort {} {',
            '    if {[info commands CheckViewPort] eq ""} { return }',
            '    set __body [info body CheckViewPort]',
            '    if {[string first "CheckViewPort_running" $__body] >= 0} { return }',
            '    catch {rename CheckViewPort_saved {}}',
            '    catch {rename CheckViewPort CheckViewPort_saved}',
            '    if {[info commands CheckViewPort] ne ""} { return }',
            '    proc CheckViewPort {wv} {',
            '        global CheckViewPort_running',
            '        if {[info exists CheckViewPort_running($wv)] && $CheckViewPort_running($wv)} { return }',
            '        set CheckViewPort_running($wv) 1',
            '        if {[catch {CheckViewPort_saved $wv} err]} {',
            '            Log::Debug big "CheckViewPort error: $err"',
            '        }',
            '        set CheckViewPort_running($wv) 0',
            '    }',
            '}',
            '# --- Define delete trace handler (schedules re-guard after recreation) ---',
            'proc ::OnCheckViewPortDelete {name op} {',
            '    catch {after 0 ::ReGuardCheckViewPort}',
            '}',
            '# --- Remove stale trace, install new one ---',
                'catch {trace remove command CheckViewPort delete ::OnCheckViewPortDelete}',
                'catch {trace add command CheckViewPort delete ::OnCheckViewPortDelete}',
                '::ReGuardCheckViewPort',
        ]
        result = run_check_attempt(
            name="wrap_checkviewport",
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / "wrap_checkviewport.txt",
                "IPG-MOVIE",
                body_lines,
            ),
            timeout_sec=timeout_sec,
        )
        if not result.get("ok"):
            print(f"[INFO] could not wrap CheckViewPort (non-fatal): {result.get('detail')}")
        else:
            print("Wrapped CheckViewPort with re-entrant guard + auto-reinstall delete-trace")
    except Exception as exc:
        print(f"[INFO] wrap CheckViewPort failed (non-fatal): {exc}")


def disable_checkviewport_recursion(*, timeout_sec: float = 10.0) -> None:
    """Install a re-entrant guard on IPG-MOVIE's CheckViewPort Tcl proc to prevent
    infinite recursion ('too many nested evaluations') during the prepare+capture
    cycle. Unlike wrap_checkviewport(), this does NOT install the delete-trace;
    call wrap_checkviewport() separately for that.

    The guard renames the original CheckViewPort to CheckViewPort_saved and
    installs a wrapper that uses a per-widget re-entrant flag (CheckViewPort_running($wv))
    to prevent infinite recursion. The original CheckViewPort is still called
    normally when not re-entering.

    Idempotent: if CheckViewPort is already guarded (body contains "CheckViewPort_running"),
    this is a no-op.

    Non-fatal on failure: IPG-MOVIE may not be ready yet, or CheckViewPort may
    not exist in this version.
    """
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_check_attempt(
            name="disable_checkviewport_recursion",
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / "disable_checkviewport_recursion.txt",
                "IPG-MOVIE",
                [
                    '# --- Define re-guard proc if not already defined ---',
                    'if {[info procs ::ReGuardCheckViewPort] eq ""} {',
                    '    proc ::ReGuardCheckViewPort {} {',
                    '        if {[info commands CheckViewPort] eq ""} { return }',
                    '        set __body [info body CheckViewPort]',
                    '        if {[string first "CheckViewPort_running" $__body] >= 0} { return }',
                    '        catch {rename CheckViewPort_saved {}}',
                    '        catch {rename CheckViewPort CheckViewPort_saved}',
                    '        if {[info commands CheckViewPort] ne ""} { return }',
                    '        proc CheckViewPort {wv} {',
                    '            global CheckViewPort_running',
                    '            if {[info exists CheckViewPort_running($wv)] && $CheckViewPort_running($wv)} { return }',
                    '            set CheckViewPort_running($wv) 1',
                    '            if {[catch {CheckViewPort_saved $wv} err]} {',
                    '                Log::Debug big "CheckViewPort error: $err"',
                    '            }',
                    '            set CheckViewPort_running($wv) 0',
                    '        }',
                    '    }',
                    '}',
                    '# --- Define delete trace handler ---',
                    'if {[info procs ::OnCheckViewPortDelete] eq ""} {',
                    '    proc ::OnCheckViewPortDelete {name op} {',
                    '        catch {after 0 ::ReGuardCheckViewPort}',
                    '    }',
                    '}',
                    '# --- Install/ensure delete trace ---',
                    'catch {trace remove command CheckViewPort delete ::OnCheckViewPortDelete}',
                    'catch {trace add command CheckViewPort delete ::OnCheckViewPortDelete}',
                    '# --- Apply guard ---',
                    '::ReGuardCheckViewPort',
                ],
            ),
            timeout_sec=timeout_sec,
        )
        if not result.get("ok"):
            print(f"[INFO] could not disable CheckViewPort (non-fatal): {result.get('detail')}")
        else:
            print("Disabled CheckViewPort recursion guard for prepare+capture cycle")
    except Exception as exc:
        print(f"[INFO] disable CheckViewPort failed (non-fatal): {exc}")


def restore_checkviewport(*, timeout_sec: float = 10.0) -> None:
    """Restore IPG-MOVIE's original CheckViewPort proc after the prepare+capture
    cycle. Undoes disable_checkviewport_recursion().

    Non-fatal on failure.
    """
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_check_attempt(
            name="restore_checkviewport",
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / "restore_checkviewport.txt",
                "IPG-MOVIE",
                [
                    "catch {rename CheckViewPort {}}",
                    "catch {rename CheckViewPort_saved CheckViewPort}",
                ],
            ),
            timeout_sec=timeout_sec,
        )
        if not result.get("ok"):
            print(f"[INFO] could not restore CheckViewPort (non-fatal): {result.get('detail')}")
        else:
            print("Restored CheckViewPort to original implementation")
    except Exception as exc:
        print(f"[INFO] restore CheckViewPort failed (non-fatal): {exc}")


def install_view_sync_trace(*, timeout_sec: float = 10.0) -> None:
    """Install a Tcl execution trace on View::SetSize that auto-syncs the View()
    dict after every View::SetSize call.

    The trace fires when View::SetSize completes, reads the actual widget
    dimensions, and updates View($key) Width/Height to match. This prevents the
    mismatch that causes CheckViewPort recursion ('too many nested evaluations')
    regardless of which code path calls View::SetSize.

    The trace is attached to View::SetSize (a C++ command, not a Tcl proc), so it
    PERSISTS across IPG-MOVIE re-registering CheckViewPort via Tcl_Eval. This makes
    it fundamentally more robust than the disable-checkviewport approach which
    must catch every re-registration point.

    Non-fatal on failure.
    """
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        body_lines = [
            '# --- install_view_sync_trace is DEPRECATED ---',
            '# View::SetSize is a Tcl proc, not a C++ command; trace lost on redefinition.',
            '# CheckViewPort does not read View() dict; the dict-sync trace was irrelevant.',
            '# Use wrap_checkviewport() instead (re-entrant guard + delete-trace).',
        ]
        result = run_check_attempt(
            name="install_view_sync_trace",
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / "install_view_sync_trace.txt",
                "IPG-MOVIE",
                body_lines,
            ),
            timeout_sec=timeout_sec,
        )
        if not result.get("ok"):
            print(f"[INFO] could not install View::SetSize trace (non-fatal): {result.get('detail')}")
        else:
            print("Installed persistent View::SetSize trace to auto-sync View() dict")
    except Exception as exc:
        print(f"[INFO] install View::SetSize trace failed (non-fatal): {exc}")


def remove_view_sync_trace(*, timeout_sec: float = 10.0) -> None:
    """Remove the View::SetSize trace and cleanup the callback proc.
    Non-fatal on failure.
    """
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_check_attempt(
            name="remove_view_sync_trace",
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / "remove_view_sync_trace.txt",
                "IPG-MOVIE",
                [
                    '# --- remove_view_sync_trace is DEPRECATED. Still cleans up stale traces. ---',
                    'catch {trace remove execution View::SetSize leave ::ViewSyncAfterSetSize}',
                    'catch {rename ::ViewSyncAfterSetSize {}}',
                ],
            ),
            timeout_sec=timeout_sec,
        )
        if not result.get("ok"):
            print(f"[INFO] could not remove View::SetSize trace (non-fatal): {result.get('detail')}")
        else:
            print("Removed View::SetSize trace")
    except Exception as exc:
        print(f"[INFO] remove View::SetSize trace failed (non-fatal): {exc}")


async def start_or_reuse_carmaker_for_open_movie(
    cm_install: Path,
    host: str,
    project_root: Path,
    clean_existing_processes: bool,
) -> tuple[Optional[cmapi.CarMaker], Optional[int], bool, str]:
    existing_carmakers = list_carmaker_processes()
    runtime_carmakers = list_runtime_carmaker_processes(existing_carmakers)
    expected_project_root = project_root.resolve()

    if len(runtime_carmakers) == 1:
        pid = int(runtime_carmakers[0]["ProcessId"])
        running_project_root = probe_running_carmaker_projectdir()
        if running_project_root == expected_project_root:
            return attach_to_existing_carmaker(pid, host, expected_project_root), pid, False, (
                f"reused existing PID {pid} for projectdir {expected_project_root.as_posix()}"
            )

    if len(runtime_carmakers) > 1:
        if clean_existing_processes:
            killed = kill_existing_cm_processes()
            summary = summarize_processes(killed)
            executable = resolve_carmaker_executable(cm_install)
            subprocess.Popen(
                [str(executable), "-projectdir", expected_project_root.as_posix()],
                cwd=str(executable.parent),
            )
            wait_for_carmaker_tcleval_ready()
            return None, None, True, f"cleared conflicting processes and started HIL GUI only: {summary}"
        raise RuntimeError(
            "Multiple CarMaker instances are running. Re-run with cleanup enabled to reset the stack."
        )

    if len(existing_carmakers) == 1 and existing_carmakers[0].get("Name") == "HIL.exe":
        running_project_root = probe_running_carmaker_projectdir()
        if running_project_root is None or running_project_root == expected_project_root:
            wait_for_carmaker_tcleval_ready()
            return None, None, False, f"reused existing HIL GUI for projectdir {expected_project_root.as_posix()}"
        if clean_existing_processes:
            killed = kill_existing_cm_processes()
            summary = summarize_processes(killed)
            executable = resolve_carmaker_executable(cm_install)
            subprocess.Popen(
                [str(executable), "-projectdir", expected_project_root.as_posix()],
                cwd=str(executable.parent),
            )
            wait_for_carmaker_tcleval_ready()
            return None, None, True, f"restarted HIL GUI after projectdir mismatch: {summary}"

    if not existing_carmakers:
        executable = resolve_carmaker_executable(cm_install)
        subprocess.Popen(
            [str(executable), "-projectdir", expected_project_root.as_posix()],
            cwd=str(executable.parent),
        )
        wait_for_carmaker_tcleval_ready()
        return None, None, True, "started new HIL GUI instance without prestarting runtime"

    if clean_existing_processes:
        killed = kill_existing_cm_processes()
        summary = summarize_processes(killed)
        executable = resolve_carmaker_executable(cm_install)
        subprocess.Popen(
            [str(executable), "-projectdir", expected_project_root.as_posix()],
            cwd=str(executable.parent),
        )
        wait_for_carmaker_tcleval_ready()
        return None, None, True, f"cleared conflicting processes and started HIL GUI only: {summary}"

    raise RuntimeError(
        f"Cannot reuse existing CarMaker GUI state for {expected_project_root.as_posix()}."
    )


def wait_for_runtime_carmaker_pid(
    project_root: Path,
    timeout_sec: float = 60.0,
    poll_interval_sec: float = 0.5,
) -> int:
    expected_project_root = project_root.resolve()
    deadline = time.monotonic() + timeout_sec
    last_summary = "none"
    while time.monotonic() < deadline:
        processes = list_carmaker_processes()
        last_summary = summarize_processes(processes)
        runtime_processes = list_runtime_carmaker_processes(processes)
        if len(runtime_processes) == 1:
            running_project_root = probe_running_carmaker_projectdir(timeout_sec=1.0)
            if running_project_root is None or running_project_root == expected_project_root:
                return int(runtime_processes[0]["ProcessId"])
        time.sleep(poll_interval_sec)

    raise RuntimeError(
        "Timed out waiting for CarMaker runtime process for "
        f"{expected_project_root.as_posix()}. Visible processes: {last_summary}"
    )


def resolve_attached_carmaker_pid(carmaker: cmapi.CarMaker, project_root: Path) -> int:
    pid = carmaker.get_pid()
    if pid is not None:
        return int(pid)
    return wait_for_runtime_carmaker_pid(project_root, timeout_sec=20.0, poll_interval_sec=0.25)


def build_gui_movie_command(
    cm_install: Path,
    movie_apphost: str,
    project_root: Path,
    carmaker_pid: int,
) -> list[str]:
    movie_executable = require_file(cm_install / "GUI" / "Movie.exe", "IPG-MOVIE executable")
    return [
        str(movie_executable),
        "-CMInstance",
        "0",
        "-apphost",
        movie_apphost,
        "-apppid",
        str(carmaker_pid),
        "-projectdir",
        project_root.resolve().as_posix(),
        "-datapool",
        cm_install.resolve().as_posix(),
        "-cmgui",
        "CarMaker",
    ]


def wait_for_gui_movie_pid(
    existing_pids: set[int],
    timeout_sec: float = 15.0,
    poll_interval_sec: float = 0.25,
) -> int:
    deadline = time.monotonic() + timeout_sec
    last_summary = "none"
    while time.monotonic() < deadline:
        processes = list_gui_movie_processes()
        last_summary = summarize_processes(processes)
        new_processes = [proc for proc in processes if int(proc["ProcessId"]) not in existing_pids]
        if len(new_processes) == 1:
            return int(new_processes[0]["ProcessId"])
        time.sleep(poll_interval_sec)
    raise RuntimeError(f"Timed out waiting for GUI IPG-MOVIE startup. Visible GUI Movie processes: {last_summary}")


def restart_gui_movie_for_send_recovery(
    cm_install: Path,
    movie_apphost: str,
    project_root: Path,
    carmaker_pid: int,
) -> int:
    before = snapshot_movie_stack()
    stop_movie_stack_via_movie_quit(
        timeout_sec=DEFAULT_MOVIE_QUIT_TIMEOUT_SEC,
        probe_name="cmapi_testrun_control_movie_quit_send_recovery",
    )
    existing_pids = set(before["gui"])
    command = build_gui_movie_command(cm_install, movie_apphost, project_root, carmaker_pid)
    subprocess.Popen(command, cwd=str((cm_install / "GUI").resolve()))
    movie_pid = wait_for_gui_movie_pid(existing_pids)
    return movie_pid


def restart_movie_rendering(*, timeout_sec: float = 10.0) -> None:
    """Restart IPG-MOVIE rendering loop if frozen.

    Cancels existing timer, resets state, schedules a new
    UpdateView_TimerProc via ``after 10`` with UpdateViewActive=1.

    The standard RestartUpdateView proc often does not respond
    after a Tcl error, so we use direct after scheduling.

    Non-fatal on failure.
    """
    body = [
        "# Cancel existing timer and install no-op guard",
        "catch {after cancel UpdateView_TimerProc}",
        "catch {rename UpdateView_TimerProc __saved_uvp_restart}",
        "proc UpdateView_TimerProc {args} {}",
        "update",
        "# Reset render state",
        "set ::View(StopUpdateView) 0",
        "set ::View(UpdateViewActive) 0",
        "# Restore original proc (directly overwrite no-op — no delete step)",
        "catch {rename __saved_uvp_restart UpdateView_TimerProc}",
        "# Schedule rendering",
        "after 10 {set ::View(UpdateViewActive) 1; UpdateView_TimerProc}",
    ]
    try:
        result = run_check_attempt(
            name="restart_movie_rendering",
            service="TclEval",
            topic="CarMaker",
            output_dir=Path("tmp"),
            script_text=render_dde_execute_script(
                Path("tmp") / "restart_movie_rendering.txt",
                "IPG-MOVIE",
                body,
            ),
            timeout_sec=timeout_sec,
        )
        if result.get("ok"):
            print("Restarted IPG-MOVIE rendering via direct after scheduling")
        else:
            print(f"[INFO] restart movie rendering non-fatal: {result.get('detail')}")
    except Exception as exc:
        print(f"[INFO] restart movie rendering failed (non-fatal): {exc}")


def _parse_probe_detail(detail: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for chunk in detail.split(";"):
        key, separator, value = chunk.partition("=")
        if not separator:
            continue
        payload[key.strip()] = value.strip()
    return payload


def probe_movie_runtime_registration(timeout_sec: float) -> dict[str, str]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_check_attempt(
        name=f"{MOVIE_SCENE_READY_PROBE_NAME}_runtime",
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        script_text=render_result_script(
            output_dir / f"{MOVIE_SCENE_READY_PROBE_NAME}_runtime.txt",
            [
                'set exact [WInfoInterps "IPG-MOVIE"]',
                'set gpu [WInfoInterps "GPUSensor_*"]',
                'format "exact=%s;gpu=%s" $exact $gpu',
            ],
        ),
        timeout_sec=timeout_sec,
    )
    if not result.get("ok"):
        raise RuntimeError(f"{result.get('kind')}: {result.get('detail')}")
    return _parse_probe_detail(str(result.get("detail") or "").strip())


def _get_health_check(summary: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    for check in summary.get("checks", []):
        if check.get("name") == name:
            return check
    return None


def classify_gui_movie_send_health(summary: dict[str, Any]) -> dict[str, Any]:
    classification = classify_health_summary(summary)
    return {
        "all_ok": classification.get("code") == "ok",
        **classification,
    }


def run_movie_send_health_check(
    attempts: int,
    timeout_sec: float,
    settle_sec: float,
) -> dict[str, Any]:
    output_dir = default_output_dir() / MOVIE_SEND_HEALTH_CHECK_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    return run_read_only_health_suite(
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        attempts=max(1, attempts),
        timeout_sec=max(0.1, timeout_sec),
        settle_sec=max(0.0, settle_sec),
    )


async def recover_movie_send_surface_after_health_failure(
    cm_install: Path,
    movie_apphost: str,
    project_root: Path,
    testrun_rel_path: Path,
    carmaker_pid: int,
    clean_existing_processes: bool,
    running_timeout_sec: float,
    idle_timeout_sec: float,
    scene_timeout_sec: float,
    scene_poll_interval_sec: float,
    health_attempts: int,
    health_timeout_sec: float,
    health_settle_sec: float,
) -> tuple[Optional[cmapi.IPGMovie], Optional[int], bool, str, dict[str, str], dict[str, Any]]:
    movie_reset = stop_movie_stack_via_movie_quit(
        timeout_sec=DEFAULT_MOVIE_QUIT_TIMEOUT_SEC,
        probe_name="cmapi_testrun_control_movie_quit_health_recovery",
    )
    killed_summary = (
        f"mode={movie_reset.get('mode')} before_gui={movie_reset.get('before', {}).get('gui', [])} "
        f"before_gpu={movie_reset.get('before', {}).get('gpu', [])}"
    )
    movie, movie_pid, movie_owned, movie_action = await start_or_reuse_movie(
        cm_install,
        movie_apphost,
        project_root,
        carmaker_pid,
        clean_existing_processes,
    )
    movie_scene = wait_for_movie_scene_ready(
        cm_install=cm_install,
        movie_apphost=movie_apphost,
        project_root=project_root,
        carmaker_pid=carmaker_pid,
        timeout_sec=scene_timeout_sec,
        poll_interval_sec=scene_poll_interval_sec,
    )
    health_summary = run_movie_send_health_check(
        attempts=health_attempts,
        timeout_sec=health_timeout_sec,
        settle_sec=health_settle_sec,
    )
    recovery_action = (
        f"restarted GUI Movie after remote-control health failure: killed={killed_summary}; movie_action={movie_action}"
    )
    return movie, movie_pid, movie_owned, recovery_action, movie_scene, health_summary


def wait_for_movie_scene_ready(
    cm_install: Path,
    movie_apphost: str,
    project_root: Path,
    carmaker_pid: int,
    timeout_sec: float,
    poll_interval_sec: float,
    initial_grace_sec: float = DEFAULT_MOVIE_SCENE_READY_GRACE_SEC,
) -> dict[str, str]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_sec
    grace_deadline = time.monotonic() + max(0.0, min(initial_grace_sec, timeout_sec))
    last_detail = "not_ready"
    send_failure_count = 0
    restart_attempted = False
    while time.monotonic() < deadline:
        remaining_sec = max(0.5, min(3.0, deadline - time.monotonic()))
        result = run_check_attempt(
            name=MOVIE_SCENE_READY_PROBE_NAME,
            service="TclEval",
            topic="CarMaker",
            output_dir=output_dir,
            script_text=render_dde_execute_script(
                output_dir / f"{MOVIE_SCENE_READY_PROBE_NAME}.txt",
                "IPG-MOVIE",
                [
                    'if {![info exists View(ev.view)]} {error "missing View(ev.view)"}',
                    'set view_key $View(ev.view)',
                    'scan $view_key %d vno',
                    'set wi [dict get $View($view_key) Width]',
                    'set he [dict get $View($view_key) Height]',
                    'set abraxas_menu ".view${vno}.mbar.view.m.show"',
                    'set abraxas_menu_ready [expr {[winfo exists $abraxas_menu] ? 1 : 0}]',
                    'set camera_widget [expr {[winfo exists .camera] ? 1 : 0}]',
                    'if {[info exists Camera::v(Name)]} {set camera_name $Camera::v(Name)} else {set camera_name ""}',
                    *_movie_background_tcl_commands(include_root=True),
                    'format "width=%s;height=%s;camera_widget=%s;camera_name=%s;abraxas_menu_ready=%s" $wi $he $camera_widget $camera_name $abraxas_menu_ready',
                ],
            ),
            timeout_sec=remaining_sec,
        )
        if result.get("ok"):
            detail = str(result.get("detail") or "").strip()
            payload = _parse_probe_detail(detail)
            width = int(payload.get("width", "0") or "0")
            height = int(payload.get("height", "0") or "0")
            camera_name = str(payload.get("camera_name", "") or "").strip()
            abraxas_menu_ready = str(payload.get("abraxas_menu_ready", "0") or "0") == "1"
            camera_scene_ready = bool(camera_name) and camera_name.casefold() != "default"
            if width > 0 and height > 0 and abraxas_menu_ready:
                payload["mode"] = "dde_execute_probe"
                return payload
            last_detail = detail or "scene_not_ready"
        else:
            last_detail = f"{result.get('kind')}: {result.get('detail')}"
            send_failure_count += 1

        if send_failure_count >= 2 and time.monotonic() < grace_deadline:
            time.sleep(max(0.1, poll_interval_sec))
            continue

        if send_failure_count >= 2 and not restart_attempted:
            try:
                restarted_pid = restart_gui_movie_for_send_recovery(
                    cm_install,
                    movie_apphost,
                    project_root,
                    carmaker_pid,
                )
                restart_attempted = True
                send_failure_count = 0
                last_detail = f"restarted_gui_movie_pid={restarted_pid}; previous={last_detail}"
                time.sleep(max(0.5, poll_interval_sec))
                continue
            except Exception as exc:
                restart_attempted = True
                last_detail = f"gui_movie_restart_failed: {exc}; previous={last_detail}"

        if send_failure_count >= 2:
            try:
                runtime_payload = probe_movie_runtime_registration(timeout_sec=remaining_sec)
                exact = runtime_payload.get("exact", "")
                gpu = runtime_payload.get("gpu", "")
                if exact == "IPG-MOVIE" and list_gui_movie_processes():
                    runtime_scope = "gui_movie_plus_gpusensor" if gpu and list_gpusensor_movie_processes() else "gui_movie_only"
                    last_detail = (
                        f"scene_send_unready exact={exact} gpu={gpu or '{}'} runtime_scope={runtime_scope} "
                        f"recovery={'gui_movie_restart' if restart_attempted else 'none'} previous={last_detail}"
                    )
            except Exception as exc:
                last_detail = str(exc)
        time.sleep(max(0.1, poll_interval_sec))

    raise RuntimeError(f"Timed out waiting for IPG-MOVIE calibration scene readiness: {last_detail}")


def wait_for_movie_runtime_online_relaxed(
    *,
    timeout_sec: float,
    poll_interval_sec: float,
) -> dict[str, str]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_prepare_movie_runtime_online_probe"
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
                    *_movie_background_tcl_commands(include_root=True),
                    'format "width=%s;height=%s;camera_name=%s;abraxas_menu_ready=%s" $wi $he $camera_name $abraxas_menu_ready',
                ],
            ),
            timeout_sec=min(3.0, max(0.5, deadline - time.monotonic())),
        )
        if result.get("ok"):
            detail = str(result.get("detail") or "").strip()
            payload = _parse_probe_detail(detail)
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


def ensure_movie_view_size(
    width: int,
    height: int,
    *,
    service: str = "TclEval",
    topic: str = "CarMaker",
    timeout_sec: float = 8.0,
) -> dict[str, str]:
    target_width = int(width)
    target_height = int(height)
    if target_width <= 0 or target_height <= 0:
        raise ValueError(f"Movie view size must be positive, got {target_width}x{target_height}")

    # --- 自动减半以适配 IPG-MOVIE 显示屏 ---
    try:
        display_w = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
        display_h = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
    except Exception:
        display_w, display_h = 1920, 1080
    max_w = display_w - 50
    max_h = display_h - 50
    while target_width > max_w or target_height > max_h:
        target_width //= 2
        target_height //= 2
        if target_width < 64 or target_height < 64:
            break
    if target_width != width or target_height != height:
        logger.warning(
            f"Movie view size auto-reduced from {width}x{height} to "
            f"{target_width}x{target_height} to fit display safe area {display_w-50}x{display_h-50}"
        )

    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_testrun_control_movie_view_size_probe"
    result = run_check_attempt(
        name=probe_name,
        service=service,
        topic=topic,
        output_dir=output_dir,
        script_text=render_dde_execute_script(
            output_dir / f"{probe_name}.txt",
            "IPG-MOVIE",
            [
                'if {[info exists View(ev.view)]} {',
                '    scan $View(ev.view) %d wno',
                '    set wpath ".view$wno"',
                '} else {',
                '    set wpath ".view0"',
                '    set wno 0',
                '}',
                '# Temporarily restore window if minimized (View::SetSize needs GL context for Configure event)',
                'if {[catch {set __state [wm state .]}]} { set __state "" }',
                'set __was_iconic [expr {$__state eq "iconic"}]',
                'if {$__was_iconic} {',
                '    wm state . normal',
                '    update',
                '    update idletasks',
                '}',
                '# Skip View::SetSize if already at target size (avoids C++ ConfigFBO on GL context)',
                'set __cur_w [$wpath.gl0 cget -width]',
                'set __cur_h [$wpath.gl0 cget -height]',
                f'if {{$__cur_w != {target_width} || $__cur_h != {target_height}}} {{',
                f'    View::SetSize {target_width} {target_height} $wpath',
                f'    if {{[info exists View($wno)]}} {{ set ::View($wno) [dict replace $::View($wno) Width {target_width} Height {target_height}] }}',
                '    update',
                '    update idletasks',
                '}',
                'set wi [$wpath.gl0 cget -width]',
                'set he [$wpath.gl0 cget -height]',
                '# Re-minimize if window was iconic before',
                'if {$__was_iconic} { wm state . iconic; update }',
                'format "width=%s;height=%s;widget=%s" $wi $he $wpath',
            ],
        ),
        timeout_sec=max(1.0, float(timeout_sec)),
    )
    if not result.get("ok"):
        raise RuntimeError(f"Failed to set IPG-MOVIE view size: {result.get('kind')}: {result.get('detail')}")

    detail = str(result.get("detail") or "").strip()
    payload = _parse_probe_detail(detail)
    actual_width = int(payload.get("width", "0") or "0")
    actual_height = int(payload.get("height", "0") or "0")
    if actual_width != target_width or actual_height != target_height:
        logger.warning(
            "IPG-MOVIE view size mismatch: "
            f"expected={target_width}x{target_height}, actual={actual_width}x{actual_height}"
        )
    payload["mode"] = "view_size_applied"
    return payload


def ensure_movie_abraxas_enabled(
    *,
    service: str = "TclEval",
    topic: str = "CarMaker",
    timeout_sec: float = 8.0,
) -> dict[str, str]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_testrun_control_movie_abraxas_probe"
    result = run_check_attempt(
        name=probe_name,
        service=service,
        topic=topic,
        output_dir=output_dir,
        script_text=render_dde_execute_script(
            output_dir / f"{probe_name}.txt",
            "IPG-MOVIE",
            [
                'if {![info exists View(ev.view)]} {error "missing View(ev.view)"}',
                'scan $View(ev.view) %d vno',
                'set menu ".view${vno}.mbar.view.m.show"',
                'if {![winfo exists $menu]} {error "missing ABRAXAS menu"}',
                'set before [expr {[info exists View(ABRAXAS)] ? $View(ABRAXAS) : -1}]',
                'if {$before != 1} {$menu invoke 1}',
                'update',
                'update idletasks',
                'set after [expr {[info exists View(ABRAXAS)] ? $View(ABRAXAS) : -1}]',
                'format "before=%s;after=%s;menu=%s;view=%s" $before $after $menu $vno',
            ],
        ),
        timeout_sec=max(1.0, float(timeout_sec)),
    )
    if not result.get("ok"):
        raise RuntimeError(f"Failed to enable IPG-MOVIE ABRAXAS: {result.get('kind')}: {result.get('detail')}")

    detail = str(result.get("detail") or "").strip()
    payload = _parse_probe_detail(detail)
    if str(payload.get("after") or "0") != "1":
        raise RuntimeError("IPG-MOVIE ABRAXAS did not stay enabled")
    payload["mode"] = "abraxas_enabled"
    return payload


def ensure_movie_camera_selected(
    sensor_label: str,
    *,
    service: str = "TclEval",
    topic: str = "CarMaker",
    timeout_sec: float = 8.0,
) -> dict[str, str]:
    target_label = str(sensor_label or "").strip()
    if not target_label:
        raise ValueError("IPG-MOVIE sensor label must not be empty")

    escaped_label = target_label.replace("\\", "\\\\").replace('"', '\\"')
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_testrun_control_movie_camera_select_probe"
    select_body_lines = [
        'set _before_camera_state [expr {[winfo exists .camera] ? [wm state .camera] : "missing"}]',
        'set _before_lens_state [expr {[winfo exists .camera.cammoddlg] ? [wm state .camera.cammoddlg] : "missing"}]',
        'if {![info exists View(ev.view)]} {error "missing View(ev.view)"}',
        'if {![info exists View(ev.view)]} {error "missing View(ev.view)"}',
        'set vno $View(ev.view)',
        'if {![winfo exists .camera] || ![winfo exists .camera.btn.set]} {',
        '    Camera::ShowSettingsDlg',
        '    update',
        '    update idletasks',
        '}',
        f'set target "{escaped_label}"',
        'Camera::Select $target $vno',
        'update',
        'update idletasks',
        'if {![winfo exists .camera.btn.set]} {error "missing widget .camera.btn.set"}',
        '.camera.btn.set invoke',
        'update',
        'update idletasks',
        '# wm lower/iconify removed — ineffective and triggers ConfigureNotify → NaN',
        'if {$_before_camera_state eq "iconic" && [winfo exists .camera]} { wm iconify .camera }',
        'if {$_before_lens_state eq "iconic" && [winfo exists .camera.cammoddlg]} { wm iconify .camera.cammoddlg }',
        'unset _before_camera_state _before_lens_state',
        '# Ensure render timer is running after camera selection',
        'catch {after 0 UpdateView_TimerProc}',
        'if {[info exists Camera::v(Name)]} {set current $Camera::v(Name)} else {set current ""}',
        'format "state=selected;selected=%s;current=%s;view=%s;apply_invoked=1" $target $current $vno',
    ]
    result = run_check_attempt(
        name=probe_name,
        service=service,
        topic=topic,
        output_dir=output_dir,
        script_text=render_dde_execute_script(
            output_dir / f"{probe_name}.txt",
            "IPG-MOVIE",
            select_body_lines,
        ),
        timeout_sec=max(1.0, float(timeout_sec)),
    )
    if not result.get("ok"):
        raise RuntimeError(f"Failed to select IPG-MOVIE camera sensor {target_label}: {result.get('kind')}: {result.get('detail')}")

    detail = str(result.get("detail") or "").strip()
    payload = _parse_probe_detail(detail)
    if payload.get("state") != "selected":
        raise RuntimeError(
            "IPG-MOVIE camera sensor selection did not report selected state: "
            f"requested={target_label}, detail={detail or '<empty>'}"
        )
    current_label = str(payload.get("current") or "")
    target_key = re.sub(r"^camera_rsi-sensor\s+vh(?:cl|ic)\.", "", target_label, flags=re.IGNORECASE).casefold()
    current_key = re.sub(r"^camera_rsi-sensor\s+vh(?:cl|ic)\.", "", current_label, flags=re.IGNORECASE).casefold()
    if current_label != target_label and current_key != target_key:
        raise RuntimeError(
            "IPG-MOVIE camera sensor selection did not latch to the requested sensor: "
            f"requested={target_label}, actual={current_label or '<empty>'}"
        )
    if str(payload.get("apply_invoked") or "0") != "1":
        raise RuntimeError(
            "IPG-MOVIE camera sensor selection did not apply through Camera Settings Add/Set; "
            f"requested={target_label}, current={current_label or '<empty>'}"
        )
    if payload.get("render_fallback") == "1":
        payload["mode"] = "camera_select_apply_verified"
    else:
        payload["mode"] = "camera_select_render_verified"
    return payload


def ensure_movie_camera_widgets(
    *,
    service: str = "TclEval",
    topic: str = "CarMaker",
    timeout_sec: float = 8.0,
) -> dict[str, str]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_testrun_control_movie_camera_widgets_probe"
    result = run_check_attempt(
        name=probe_name,
        service=service,
        topic=topic,
        output_dir=output_dir,
        script_text=render_dde_execute_script(
            output_dir / f"{probe_name}.txt",
            "IPG-MOVIE",
            [
                'set before_camera [expr {[winfo exists .camera] ? 1 : 0}]',
                'set before_lens [expr {[winfo exists .camera.cammoddlg] ? 1 : 0}]',
                'if {!$before_camera} {',
                '    Camera::ShowSettingsDlg',
                '    update',
                '    update idletasks',
                '}',
                'if {[winfo exists .camera.cammoddlg]} {',
                '    if {!$before_lens} {',
                '        wm deiconify .camera.cammoddlg',
                '    }',
                '} elseif {[winfo exists .camera.fmore.bcammod]} {',
                '    .camera.fmore.bcammod invoke',
                '}',
                'update',
                'update idletasks',
                *_movie_background_tcl_commands(include_root=True),
                'set after_camera [expr {[winfo exists .camera] ? 1 : 0}]',
                'set after_lens [expr {[winfo exists .camera.cammoddlg] ? 1 : 0}]',
                'set lens_state [expr {[winfo exists .camera.cammoddlg] ? [wm state .camera.cammoddlg] : "missing"}]',
                'format "before_camera=%s;before_lens=%s;after_camera=%s;after_lens=%s;lens_state=%s" $before_camera $before_lens $after_camera $after_lens $lens_state',
            ],
        ),
        timeout_sec=max(1.0, float(timeout_sec)),
    )
    if not result.get("ok"):
        raise RuntimeError(
            "Failed to initialize IPG-MOVIE Camera Settings/Lens Parameters: "
            f"{result.get('kind')}: {result.get('detail')}"
        )

    detail = str(result.get("detail") or "").strip()
    payload = _parse_probe_detail(detail)
    if str(payload.get("after_camera") or "0") != "1":
        raise RuntimeError("IPG-MOVIE Camera Settings dialog did not open")
    if str(payload.get("after_lens") or "0") != "1":
        raise RuntimeError("IPG-MOVIE Camera Lens Parameters dialog did not initialize")
    payload["mode"] = "camera_widgets_ready"
    return payload


def ensure_movie_camera_dialogs_normal(
    *,
    service: str = "TclEval",
    topic: str = "CarMaker",
    timeout_sec: float = 8.0,
) -> dict[str, str]:
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_name = "cmapi_testrun_control_camera_dialogs_normal_probe"
    result = run_check_attempt(
        name=probe_name,
        service=service,
        topic=topic,
        output_dir=output_dir,
        script_text=render_dde_execute_script(
            output_dir / f"{probe_name}.txt",
            "IPG-MOVIE",
            [
                'set before_camera [expr {[winfo exists .camera] ? 1 : 0}]',
                'set before_lens [expr {[winfo exists .camera.cammoddlg] ? 1 : 0}]',
                'if {!$before_camera} {',
                '    Camera::ShowSettingsDlg',
                '    update',
                '    update idletasks',
                '}',
                "update",
                "update idletasks",
                'if {![winfo exists .camera.cammoddlg] && [winfo exists .camera.fmore.bcammod]} {',
                '    .camera.fmore.bcammod invoke',
                '}',
                "update",
                "update idletasks",
                *_movie_background_tcl_commands(include_root=True),
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
            "Failed to ensure IPG-MOVIE Camera Settings/Lens Parameters: "
            f"{result.get('kind')}: {result.get('detail')}"
        )

    payload = _parse_probe_detail(str(result.get("detail") or "").strip())
    if payload.get("camera_exists") != "1":
        raise RuntimeError("IPG-MOVIE Camera Settings dialog is missing after deiconify probe")
    if payload.get("lens_exists") != "1":
        raise RuntimeError("IPG-MOVIE Camera Lens Parameters dialog is missing after deiconify probe")
    if payload.get("camera_state") not in ("normal", "iconic", "withdrawn"):
        raise RuntimeError(f"IPG-MOVIE Camera Settings dialog is not in normal or iconic state: {payload.get('camera_state')}")
    if payload.get("lens_state") not in ("normal", "iconic", "withdrawn"):
        raise RuntimeError(f"IPG-MOVIE Camera Lens Parameters dialog is not in normal or iconic state: {payload.get('lens_state')}")

    payload["mode"] = "camera_dialogs_normal"
    return payload


def attach_to_existing_carmaker(pid: int, host: str, project_root: Path) -> cmapi.CarMaker:
    cmapi.Project.load(project_root.resolve())
    carmaker = cmapi.CarMaker()
    carmaker.set_host(host)
    carmaker.set_sinfo(cmapi.ApoServerInfo(pid=pid, description="Idle"))
    carmaker.set_state(cmapi.AppState.started)
    return carmaker


async def start_carmaker(cm_install: Path, host: str, project_root: Path) -> cmapi.CarMaker:
    executable = resolve_carmaker_executable(cm_install)
    if executable.name.lower() == "hil.exe":
        subprocess.Popen(
            [str(executable), "-projectdir", project_root.resolve().as_posix()],
            cwd=str(executable.parent),
        )
        runtime_pid = wait_for_runtime_carmaker_pid(project_root, timeout_sec=90.0, poll_interval_sec=0.5)
        return attach_to_existing_carmaker(runtime_pid, host, project_root)

    carmaker = cmapi.CarMaker()
    carmaker.set_host(host)
    carmaker.set_executable_path(executable)
    carmaker.set_arg("-projectdir", project_root.resolve().as_posix())
    await carmaker.start()
    return carmaker


async def start_or_reuse_carmaker(
    cm_install: Path,
    host: str,
    project_root: Path,
    clean_existing_processes: bool,
) -> tuple[cmapi.CarMaker, int, bool, str]:
    existing_carmakers = list_carmaker_processes()
    runtime_carmakers = list_runtime_carmaker_processes(existing_carmakers)
    expected_project_root = project_root.resolve()

    if len(runtime_carmakers) == 1:
        pid = int(runtime_carmakers[0]["ProcessId"])
        running_project_root = probe_running_carmaker_projectdir()
        if running_project_root == expected_project_root:
            return attach_to_existing_carmaker(pid, host, expected_project_root), pid, False, (
                f"reused existing PID {pid} for projectdir {expected_project_root.as_posix()}"
            )

        if running_project_root is None:
            mismatch_detail = "existing CarMaker projectdir could not be verified"
        else:
            mismatch_detail = (
                f"existing CarMaker projectdir is {running_project_root.as_posix()}, "
                f"expected {expected_project_root.as_posix()}"
            )

        if clean_existing_processes:
            killed = kill_existing_cm_processes()
            summary = summarize_processes(killed)
            carmaker = await start_carmaker(cm_install, host, expected_project_root)
            return carmaker, resolve_attached_carmaker_pid(carmaker, expected_project_root), True, (
                f"restarted CarMaker for projectdir {expected_project_root.as_posix()} after validation failure: "
                f"{mismatch_detail}; cleared conflicting processes: {summary}"
            )
        raise RuntimeError(
            f"Cannot reuse existing CarMaker: {mismatch_detail}. Re-run with cleanup enabled to reopen "
            f"{expected_project_root.as_posix()}."
        )

    if len(runtime_carmakers) > 1:
        if clean_existing_processes:
            killed = kill_existing_cm_processes()
            summary = summarize_processes(killed)
            carmaker = await start_carmaker(cm_install, host, expected_project_root)
            return carmaker, resolve_attached_carmaker_pid(carmaker, expected_project_root), True, f"cleared conflicting processes: {summary}"
        raise RuntimeError(
            "Multiple CarMaker instances are running. Re-run with cleanup enabled to reset the stack."
        )

    if len(existing_carmakers) == 1 and existing_carmakers[0].get("Name") == "HIL.exe":
        try:
            pid = wait_for_runtime_carmaker_pid(expected_project_root, timeout_sec=20.0)
            return attach_to_existing_carmaker(pid, host, expected_project_root), pid, False, (
                f"reused runtime PID {pid} that came up behind existing HIL for projectdir {expected_project_root.as_posix()}"
            )
        except RuntimeError:
            pass

    carmaker = await start_carmaker(cm_install, host, expected_project_root)
    return carmaker, resolve_attached_carmaker_pid(carmaker, expected_project_root), True, "started new CarMaker instance"


async def start_movie(
    cm_install: Path,
    movie_apphost: str,
    project_root: Path,
    carmaker_pid: int,
) -> int:
    existing_pids = {int(proc["ProcessId"]) for proc in list_gui_movie_processes()}
    command = build_gui_movie_command(cm_install, movie_apphost, project_root, carmaker_pid)
    subprocess.Popen(command, cwd=str((cm_install / "GUI").resolve()))
    return wait_for_gui_movie_pid(existing_pids)


async def start_or_reuse_movie(
    cm_install: Path,
    movie_apphost: str,
    project_root: Path,
    carmaker_pid: int,
    clean_existing_processes: bool,
) -> tuple[Optional[cmapi.IPGMovie], Optional[int], bool, str]:
    existing_gui_movies = list_gui_movie_processes()

    if len(existing_gui_movies) == 1:
        pid = int(existing_gui_movies[0]["ProcessId"])
        return None, pid, False, f"reused existing GUI IPG-MOVIE PID {pid}"

    if len(existing_gui_movies) > 1:
        if clean_existing_processes:
            movie_reset = stop_movie_stack_via_movie_quit(
                timeout_sec=DEFAULT_MOVIE_QUIT_TIMEOUT_SEC,
                probe_name="cmapi_testrun_control_movie_quit_conflicting_gui",
            )
            summary = (
                f"mode={movie_reset.get('mode')} before_gui={movie_reset.get('before', {}).get('gui', [])} "
                f"before_gpu={movie_reset.get('before', {}).get('gpu', [])}"
            )
            movie_pid = await start_movie(cm_install, movie_apphost, project_root, carmaker_pid)
            return None, movie_pid, True, f"cleared conflicting GUI IPG-MOVIE processes: {summary}"
        raise RuntimeError(
            "Multiple GUI IPG-MOVIE instances are running. Re-run with cleanup enabled to reset them."
        )

    movie_pid = await start_movie(cm_install, movie_apphost, project_root, carmaker_pid)
    return None, movie_pid, True, "started new GUI IPG-MOVIE instance"


def run_tcl_sim_command(
    *,
    commands: list[str],
    probe_name: str,
    timeout_sec: float,
) -> dict[str, Any]:
    wait_for_carmaker_tcleval_ready()
    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    return run_check_attempt(
        name=probe_name,
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        script_text=render_result_script(
            output_dir / f"{probe_name}.txt",
            [
                *commands,
                "set status ok",
            ],
        ),
        timeout_sec=max(1.0, float(timeout_sec)),
    )


def start_simulation_via_tcl(*, running_timeout_sec: float, probe_name: str) -> dict[str, Any]:
    result = run_tcl_sim_command(
        commands=[
            "StartSim",
            "update",
            "update idletasks",
        ],
        probe_name=probe_name,
        timeout_sec=max(10.0, float(running_timeout_sec) + 5.0),
    )
    if not result.get("ok"):
        raise RuntimeError(f"Failed to invoke StartSim: {result.get('kind')}: {result.get('detail')}")
    return wait_for_carmaker_status(
        "running",
        max(1000, int(max(1.0, float(running_timeout_sec)) * 1000)),
        probe_name=f"{probe_name}_running",
    )


def stop_simulation_via_tcl(*, idle_timeout_sec: float, probe_name: str) -> dict[str, Any]:
    result = run_tcl_sim_command(
        commands=[
            "StopSim",
            "update",
            "update idletasks",
        ],
        probe_name=probe_name,
        timeout_sec=max(10.0, float(idle_timeout_sec) + 5.0),
    )
    if not result.get("ok"):
        raise RuntimeError(f"Failed to invoke StopSim: {result.get('kind')}: {result.get('detail')}")
    return wait_for_carmaker_status(
        "idle",
        max(1000, int(max(1.0, float(idle_timeout_sec)) * 1000)),
        probe_name=f"{probe_name}_idle",
    )


async def cleanup(
    movie: Optional[cmapi.IPGMovie],
    carmaker: Optional[cmapi.CarMaker],
    movie_owned: bool,
    carmaker_owned: bool,
    keep_movie_open: bool,
    keep_carmaker_open: bool,
) -> None:
    if carmaker_owned and not keep_carmaker_open:
        kill_existing_cm_processes()
        return

    if movie_owned and not keep_movie_open:
        stop_movie_stack_via_movie_quit(
            timeout_sec=DEFAULT_MOVIE_QUIT_TIMEOUT_SEC,
            probe_name="cmapi_testrun_control_movie_quit_cleanup",
        )

    if carmaker is not None and carmaker_owned and not keep_carmaker_open:
        try:
            await carmaker.stop()
        except Exception:
            pass


async def main() -> None:
    args = parse_args()
    project_root = args.project_root.resolve()
    cm_install = args.cm_install.resolve()
    testrun_rel_path = normalize_testrun_path(project_root, args.testrun)
    vehicle_path, vehicle_key = resolve_vehicle_path(project_root, testrun_rel_path)

    if args.mode == "status":
        summary = build_status_summary(
            project_root=project_root,
            cm_install=cm_install,
            testrun_rel_path=testrun_rel_path,
            vehicle_path=vehicle_path,
            vehicle_key=vehicle_key,
            camera_sensor=args.camera_sensor,
            health_check_after_start=args.health_check_after_start,
            health_check_attempts=args.health_check_attempts,
            health_check_timeout_sec=args.health_check_timeout_sec,
            health_check_settle_sec=args.health_check_settle_sec,
        )
        if args.print_summary_json:
            emit_summary_json(summary)
        else:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    if args.mode == "prepare":
        summary = await execute_prepare_mode(args)
        if args.print_summary_json:
            emit_summary_json(summary)
        else:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    sensor_activation_result: Optional[dict[str, Any]] = None
    if args.camera_sensor:
        sensor_activation_result = activate_single_vehicle_sensor(vehicle_path, args.camera_sensor)

    variation = load_variation(project_root, testrun_rel_path)

    carmaker: Optional[cmapi.CarMaker] = None
    movie: Optional[cmapi.IPGMovie] = None
    carmaker_owned = False
    movie_owned = False
    carmaker_pid: Optional[int] = None
    movie_pid: Optional[int] = None

    print(f"Project root: {project_root}")
    print(f"CarMaker install: {cm_install}")
    print(f"TestRun: Data/TestRun/{testrun_rel_path.as_posix()}")
    print(f"Vehicle: Data/Vehicle/{vehicle_key}")
    if sensor_activation_result is not None:
        print(
            "Activated vehicle sensor: "
            f"{sensor_activation_result['selected_sensor_name']} "
            f"(Sensor.{sensor_activation_result['selected_sensor_index']}.Active = 1)"
        )
        print(f"IPG-MOVIE sensor label: {sensor_activation_result['ipgmovie_sensor_label']}")
        if sensor_activation_result["changed"]:
            print(f"Vehicle file updated in place: {sensor_activation_result['vehicle_path']}")
        else:
            print("Vehicle file already matched the requested single-sensor state")

    try:
        if args.open_movie:
            carmaker, carmaker_pid, carmaker_owned, carmaker_action = await start_or_reuse_carmaker_for_open_movie(
                cm_install,
                args.host,
                project_root,
                args.clean_existing_processes,
            )
        else:
            carmaker, carmaker_pid, carmaker_owned, carmaker_action = await start_or_reuse_carmaker(
                cm_install,
                args.host,
                project_root,
                args.clean_existing_processes,
            )
        print(f"CarMaker action: {carmaker_action}")
        if carmaker_pid is not None:
            print(f"CarMaker PID: {carmaker_pid}")

        selected_testrun_name = sync_gui_testrun_selection(project_root, testrun_rel_path)
        print(f"CarMaker GUI TestRun selected: {selected_testrun_name}")

        await asyncio.sleep(args.startup_settle_sec)

        if args.open_movie:
            print("Bootstrap run: starting TestRun before IPG-MOVIE")
            if args.testrun_control_mode == "tcl":
                carmaker, carmaker_pid, bootstrapped_testrun_name = await bootstrap_testrun_for_movie_via_cmapi(
                    project_root=project_root,
                    testrun_rel_path=testrun_rel_path,
                    variation=variation,
                    running_timeout_sec=args.bootstrap_running_timeout_sec,
                    idle_timeout_sec=args.bootstrap_idle_timeout_sec,
                    apo_connect_retries=args.apo_connect_retries,
                    apo_connect_delay_sec=args.apo_connect_delay_sec,
                    host=args.host,
                    carmaker=carmaker,
                    carmaker_pid=carmaker_pid,
                )
            else:
                bootstrap_testrun_via_tk_buttons(
                    selected_testrun_name,
                    running_timeout_sec=args.bootstrap_running_timeout_sec,
                    idle_timeout_sec=args.bootstrap_idle_timeout_sec,
                )
                bootstrapped_testrun_name = selected_testrun_name
            print(
                f"Bootstrap run: {TESTRUN_CONTROL_MODE_LABELS[args.testrun_control_mode]} reached running state and returned to idle "
                f"for TestRun {bootstrapped_testrun_name}"
            )

            movie, movie_pid, movie_owned, movie_action = await start_or_reuse_movie(
                cm_install,
                args.movie_apphost,
                project_root,
                carmaker_pid,
                args.clean_existing_processes,
            )
            print(f"IPG-MOVIE action: {movie_action}")
            if movie_pid is not None:
                print(f"IPG-MOVIE PID: {movie_pid}")
            movie_scene = wait_for_movie_scene_ready(
                cm_install=cm_install,
                movie_apphost=args.movie_apphost,
                project_root=project_root,
                carmaker_pid=carmaker_pid,
                timeout_sec=args.movie_settle_sec,
                poll_interval_sec=args.movie_ready_poll_sec,
                initial_grace_sec=args.movie_ready_grace_sec,
            )
            print(
                "IPG-MOVIE scene ready: "
                f"mode={movie_scene.get('mode', 'unknown')} "
                f"recovery={movie_scene.get('recovery', 'none')} "
                f"camera_name={movie_scene.get('camera_name', '<unknown>')} "
                f"size={movie_scene.get('width', '?')}x{movie_scene.get('height', '?')} "
                f"camera_widget={movie_scene.get('camera_widget', '?')}"
            )
            abraxas = ensure_movie_abraxas_enabled(timeout_sec=args.health_check_timeout_sec)
            print(f"IPG-MOVIE ABRAXAS: before={abraxas.get('before')} after={abraxas.get('after')}")
            if sensor_activation_result is not None:
                camera_selection = ensure_movie_camera_selected(
                    sensor_activation_result["ipgmovie_sensor_label"],
                    timeout_sec=args.health_check_timeout_sec,
                )
                print(
                    "IPG-MOVIE selected camera sensor: "
                    f"requested={camera_selection.get('selected')} current={camera_selection.get('current')}"
                )
                selected_config_path = args.config_dir.resolve() / f"camera.{sensor_activation_result['selected_sensor_name']}.json"
                if selected_config_path.exists():
                    width, height = load_movie_view_size_from_real_image(selected_config_path)
                    applied_view = ensure_movie_view_size(width, height)
                    movie_scene["width"] = str(width)
                    movie_scene["height"] = str(height)
                    movie_scene["view_widget"] = str(applied_view.get("widget") or "")
            camera_widgets = ensure_movie_camera_widgets(timeout_sec=args.health_check_timeout_sec)
            print(
                "IPG-MOVIE camera widgets: "
                f"camera={camera_widgets.get('after_camera')} lens={camera_widgets.get('after_lens')} "
                f"lens_state={camera_widgets.get('lens_state')}"
            )
            if sensor_activation_result is not None:
                selected_config_path = args.config_dir.resolve() / f"camera.{sensor_activation_result['selected_sensor_name']}.json"
                if selected_config_path.exists():
                    initial_capture = capture_initial_values_to_config(selected_config_path)
                    print(
                        "IPG-MOVIE captured current initial values: "
                        f"config={selected_config_path} names={', '.join(initial_capture.get('captured_names', []))}"
                    )
            if args.health_check_after_start:
                health_summary = run_movie_send_health_check(
                    attempts=args.health_check_attempts,
                    timeout_sec=args.health_check_timeout_sec,
                    settle_sec=args.health_check_settle_sec,
                )
                classification = classify_gui_movie_send_health(health_summary)
                print(
                    "IPG-MOVIE health check: "
                    f"all_ok={classification.get('all_ok')} "
                    f"code={classification.get('code', 'unknown')}"
                )
                if not classification.get("all_ok"):
                    recoverable_codes = {
                        "movie_camera_probe_failed",
                        "movie_camera_surface_unstable",
                        "movie_commands_alive_but_tk_send_surface_failed",
                        "movie_send_targets_unresponsive",
                        "movie_send_target_registered_but_unresponsive",
                        "movie_view_probe_failed",
                        "ipg_movie_target_only_unresponsive",
                        "movie_interpreter_missing",
                    }
                    if classification.get("code") in recoverable_codes:
                        print("IPG-MOVIE health recovery: restarting GUI Movie only and retrying scene/remote-control checks")
                        movie, movie_pid, movie_owned, recovery_action, movie_scene, health_summary = await recover_movie_send_surface_after_health_failure(
                            cm_install=cm_install,
                            movie_apphost=args.movie_apphost,
                            project_root=project_root,
                            testrun_rel_path=testrun_rel_path,
                            carmaker_pid=carmaker_pid,
                            clean_existing_processes=args.clean_existing_processes,
                            running_timeout_sec=args.bootstrap_running_timeout_sec,
                            idle_timeout_sec=args.bootstrap_idle_timeout_sec,
                            scene_timeout_sec=args.movie_settle_sec,
                            scene_poll_interval_sec=args.movie_ready_poll_sec,
                            health_attempts=args.health_check_attempts,
                            health_timeout_sec=args.health_check_timeout_sec,
                            health_settle_sec=args.health_check_settle_sec,
                        )
                        classification = classify_gui_movie_send_health(health_summary)
                        print(f"IPG-MOVIE recovery action: {recovery_action}")
                        if movie_pid is not None:
                            print(f"IPG-MOVIE recovery PID: {movie_pid}")
                        print(
                            "IPG-MOVIE recovery scene: "
                            f"mode={movie_scene.get('mode', 'unknown')} "
                            f"recovery={movie_scene.get('recovery', 'none')} "
                            f"camera_name={movie_scene.get('camera_name', '<unknown>')} "
                            f"size={movie_scene.get('width', '?')}x{movie_scene.get('height', '?')}"
                        )
                        print(
                            "IPG-MOVIE recovery health check: "
                            f"all_ok={classification.get('all_ok')} "
                            f"code={classification.get('code', 'unknown')}"
                        )
                    if not classification.get("all_ok"):
                        raise RuntimeError(
                            "Post-start Movie remote-control health check failed: "
                            f"{classification.get('code', 'unknown')} | {classification.get('message', '')}"
                        )
            if args.stop_after is not None:
                print("Post-Movie stop_after request ignored because open_movie uses the manual bootstrap-only flow")
            return

        kill_movie_stack_if_gpusensor_present()
        start_simulation_via_tcl(
            running_timeout_sec=args.bootstrap_running_timeout_sec,
            probe_name="cmapi_testrun_control_run_start",
        )
        print(f"{TESTRUN_CONTROL_LABEL} reached running state")

        if args.stop_after is not None:
            await asyncio.sleep(args.stop_after)
            stop_simulation_via_tcl(
                idle_timeout_sec=args.bootstrap_idle_timeout_sec,
                probe_name="cmapi_testrun_control_run_stop",
            )
            print(f"{TESTRUN_CONTROL_LABEL} returned to idle after {args.stop_after:.3f} s")
        else:
            wait_for_carmaker_status(
                "idle",
                24 * 60 * 60 * 1000,
                probe_name="cmapi_testrun_control_run_finished",
            )
            print("Simulation finished; CarMaker returned to idle")
    finally:
        await cleanup(
            movie,
            carmaker,
            movie_owned=movie_owned,
            carmaker_owned=carmaker_owned,
            keep_movie_open=args.keep_movie_open,
            keep_carmaker_open=args.keep_carmaker_open,
        )


if __name__ == "__main__":
    cmapi.Task.run_main_task(main())
