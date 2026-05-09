from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Optional

import cmapi
from dde_health_check import default_output_dir, render_result_script, run_check_attempt


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CM_INSTALL = Path(os.environ.get("IPGHOME", "D:/IPG")) / "carmaker" / "win64-14.1"
PROCESS_ENUMERATION_COMMAND = r"""
$procs = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('CarMaker.win64.exe', 'Movie.exe') } |
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


class VehicleSensorActivationError(RuntimeError):
    pass


def _run_powershell_json(command: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
    )
    stdout = completed.stdout.strip()
    if not stdout:
        return []
    payload = json.loads(stdout)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    raise RuntimeError(f"Unexpected process enumeration payload: {payload!r}")


def list_cm_processes() -> list[dict[str, Any]]:
    return _run_powershell_json(PROCESS_ENUMERATION_COMMAND)


def list_carmaker_processes() -> list[dict[str, Any]]:
    return [proc for proc in list_cm_processes() if proc.get("Name") == "CarMaker.win64.exe"]


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
    command_line_lower = command_line.lower()
    return all(marker.lower() in command_line_lower for marker in GUI_MOVIE_MARKERS)


def list_gui_movie_processes() -> list[dict[str, Any]]:
    return [proc for proc in list_cm_processes() if is_gui_movie_process(proc)]


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


def kill_existing_cm_processes() -> list[dict[str, Any]]:
    processes = list_cm_processes()
    if not processes:
        return []

    # Reset the whole CarMaker/IPG-MOVIE stack so the next run starts from a known state.
    for image_name in ("CarMaker.win64.exe", "Movie.exe"):
        subprocess.run(
            ["taskkill", "/IM", image_name, "/F", "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
    return processes


def normalize_sensor_name(raw_value: str) -> str:
    value = raw_value.strip()
    match = IPGMOVIE_SENSOR_PREFIX_RE.match(value)
    if match:
        return match.group("name").strip()
    for prefix in ("Vhcl.", "Vhic."):
        if value.lower().startswith(prefix.lower()):
            return value[len(prefix) :].strip()
    return value


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
        "vehicle_path": vehicle_path,
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
        "--camera-sensor",
        default=None,
        help=(
            "Vehicle sensor name to activate before the run. Accepts either the plain "
            "Sensor.xx.name value or the IPG-MOVIE label CAMERA_RSI-SENSOR Vhcl.<name>."
        ),
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
        help="If set, stop the simulation after the given number of seconds.",
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
        default=2.0,
        help="Seconds to wait after IPG-MOVIE startup.",
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
    return parser.parse_args()


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
            ["emit [pwd]"],
        ),
        timeout_sec=timeout_sec,
    )
    if not result.get("ok"):
        return None

    result_path = Path(str(result["result_path"]))
    lines = result_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return None

    projectdir = lines[0].strip()
    if not projectdir:
        return None
    return Path(projectdir).resolve()


def load_variation(project_root: Path, testrun_rel_path: Path) -> cmapi.Variation:
    testrun = load_testrun(project_root, testrun_rel_path)
    return cmapi.Variation.create_from_testrun(testrun)


def attach_to_existing_carmaker(pid: int, host: str, project_root: Path) -> cmapi.CarMaker:
    cmapi.Project.load(project_root.resolve())
    carmaker = cmapi.CarMaker()
    carmaker.set_host(host)
    carmaker.set_sinfo(cmapi.ApoServerInfo(pid=pid, description="Idle"))
    carmaker.set_state(cmapi.AppState.started)
    return carmaker


async def start_carmaker(cm_install: Path, host: str, project_root: Path) -> cmapi.CarMaker:
    carmaker = cmapi.CarMaker()
    carmaker.set_host(host)
    carmaker.set_executable_path(require_file(cm_install / "bin" / "CarMaker.win64.exe", "CarMaker executable"))
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
    expected_project_root = project_root.resolve()

    if len(existing_carmakers) == 1:
        pid = int(existing_carmakers[0]["ProcessId"])
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
            summary = ", ".join(f"{proc['Name']}[{proc['ProcessId']}]" for proc in killed)
            carmaker = await start_carmaker(cm_install, host, expected_project_root)
            return carmaker, int(carmaker.get_pid()), True, (
                f"restarted CarMaker for projectdir {expected_project_root.as_posix()} after validation failure: "
                f"{mismatch_detail}; cleared conflicting processes: {summary}"
            )
        raise RuntimeError(
            f"Cannot reuse existing CarMaker: {mismatch_detail}. Re-run with cleanup enabled to reopen "
            f"{expected_project_root.as_posix()}."
        )

    if len(existing_carmakers) > 1:
        if clean_existing_processes:
            killed = kill_existing_cm_processes()
            summary = ", ".join(f"{proc['Name']}[{proc['ProcessId']}]" for proc in killed)
            carmaker = await start_carmaker(cm_install, host, expected_project_root)
            return carmaker, int(carmaker.get_pid()), True, f"cleared conflicting processes: {summary}"
        raise RuntimeError(
            "Multiple CarMaker instances are running. Re-run with cleanup enabled to reset the stack."
        )

    carmaker = await start_carmaker(cm_install, host, expected_project_root)
    return carmaker, int(carmaker.get_pid()), True, "started new CarMaker instance"


async def start_movie(cm_install: Path, host: str, carmaker: cmapi.CarMaker) -> cmapi.IPGMovie:
    movie = cmapi.IPGMovie()
    movie.set_host(host)
    movie.set_executable_path(require_file(cm_install / "GUI" / "Movie.exe", "IPG-MOVIE executable"))
    movie.attach_to_cm(carmaker)
    await movie.start()
    return movie


async def start_or_reuse_movie(
    cm_install: Path,
    host: str,
    carmaker: cmapi.CarMaker,
    clean_existing_processes: bool,
) -> tuple[Optional[cmapi.IPGMovie], Optional[int], bool, str]:
    existing_gui_movies = list_gui_movie_processes()

    if len(existing_gui_movies) == 1:
        pid = int(existing_gui_movies[0]["ProcessId"])
        return None, pid, False, f"reused existing GUI IPG-MOVIE PID {pid}"

    if len(existing_gui_movies) > 1:
        if clean_existing_processes:
            killed = kill_gui_movie_processes()
            summary = ", ".join(f"Movie.exe[{proc['ProcessId']}]" for proc in killed)
            movie = await start_movie(cm_install, host, carmaker)
            return movie, int(movie.get_pid()), True, f"cleared conflicting GUI IPG-MOVIE processes: {summary}"
        raise RuntimeError(
            "Multiple GUI IPG-MOVIE instances are running. Re-run with cleanup enabled to reset them."
        )

    movie = await start_movie(cm_install, host, carmaker)
    return movie, int(movie.get_pid()), True, "started new GUI IPG-MOVIE instance"


async def connect_simcontrol(
    carmaker_pid: int,
    host: str,
    variation: cmapi.Variation,
    retries: int,
    delay_sec: float,
) -> cmapi.SimControlInteractive:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            sinfo = cmapi.ApoServerInfo(pid=carmaker_pid, description="Idle")
            master = cmapi.ApoServer()
            master.set_sinfo(sinfo)
            master.set_host(host)

            simcontrol = await cmapi.SimControlInteractive.create_with_master(master)
            simcontrol.set_variation(variation)
            await simcontrol.connect()
            return simcontrol
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            await asyncio.sleep(delay_sec)

    raise RuntimeError(
        f"Failed to connect SimControlInteractive after {retries} attempts"
    ) from last_error


async def cleanup(
    simcontrol: Optional[cmapi.SimControlInteractive],
    movie: Optional[cmapi.IPGMovie],
    carmaker: Optional[cmapi.CarMaker],
    movie_owned: bool,
    carmaker_owned: bool,
    keep_movie_open: bool,
    keep_carmaker_open: bool,
) -> None:
    if simcontrol is not None:
        try:
            await simcontrol.disconnect()
        except Exception:
            pass

    if movie is not None and movie_owned and not keep_movie_open:
        try:
            await movie.stop()
        except Exception:
            pass

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

    sensor_activation_result: Optional[dict[str, Any]] = None
    if args.camera_sensor:
        sensor_activation_result = activate_single_vehicle_sensor(vehicle_path, args.camera_sensor)

    variation = load_variation(project_root, testrun_rel_path)

    carmaker: Optional[cmapi.CarMaker] = None
    movie: Optional[cmapi.IPGMovie] = None
    simcontrol: Optional[cmapi.SimControlInteractive] = None
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
        carmaker, carmaker_pid, carmaker_owned, carmaker_action = await start_or_reuse_carmaker(
            cm_install,
            args.host,
            project_root,
            args.clean_existing_processes,
        )
        print(f"CarMaker action: {carmaker_action}")
        print(f"CarMaker PID: {carmaker_pid}")

        await asyncio.sleep(args.startup_settle_sec)

        if args.open_movie:
            movie, movie_pid, movie_owned, movie_action = await start_or_reuse_movie(
                cm_install,
                args.host,
                carmaker,
                args.clean_existing_processes,
            )
            print(f"IPG-MOVIE action: {movie_action}")
            if movie_pid is not None:
                print(f"IPG-MOVIE PID: {movie_pid}")
            await asyncio.sleep(args.movie_settle_sec)

        simcontrol = await connect_simcontrol(
            carmaker_pid,
            args.host,
            variation,
            args.apo_connect_retries,
            args.apo_connect_delay_sec,
        )
        print("SimControlInteractive connected")

        await simcontrol.start_sim()
        print("Simulation started")

        if args.stop_after is not None:
            await asyncio.sleep(args.stop_after)
            await simcontrol.stop_sim()
            print(f"Simulation stop requested after {args.stop_after:.3f} s")
        else:
            await simcontrol.create_simstate_condition(cmapi.ConditionSimState.finished).wait()
            print("Simulation finished")
    finally:
        await cleanup(
            simcontrol,
            movie,
            carmaker,
            movie_owned=movie_owned,
            carmaker_owned=carmaker_owned,
            keep_movie_open=args.keep_movie_open,
            keep_carmaker_open=args.keep_carmaker_open,
        )


if __name__ == "__main__":
    cmapi.Task.run_main_task(main())