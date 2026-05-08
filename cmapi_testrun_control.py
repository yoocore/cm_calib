from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Optional

import cmapi


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CM_INSTALL = Path(os.environ.get("IPGHOME", "D:/IPG")) / "carmaker" / "win64-14.1"


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


def load_variation(project_root: Path, testrun_rel_path: Path) -> cmapi.Variation:
    cmapi.Project.load(project_root.resolve())
    project = cmapi.Project.instance()
    testrun = project.load_testrun_parametrization(testrun_rel_path)
    return cmapi.Variation.create_from_testrun(testrun)


async def start_carmaker(cm_install: Path, host: str) -> cmapi.CarMaker:
    carmaker = cmapi.CarMaker()
    carmaker.set_host(host)
    carmaker.set_executable_path(str(require_file(cm_install / "bin" / "CarMaker.win64.exe", "CarMaker executable")))
    await carmaker.start()
    return carmaker


async def start_movie(cm_install: Path, host: str, carmaker: cmapi.CarMaker) -> cmapi.IPGMovie:
    movie = cmapi.IPGMovie()
    movie.set_host(host)
    movie.set_executable_path(str(require_file(cm_install / "GUI" / "Movie.exe", "IPG-MOVIE executable")))
    movie.attach_to_cm(carmaker)
    await movie.start()
    return movie


async def connect_simcontrol(
    carmaker: cmapi.CarMaker,
    host: str,
    variation: cmapi.Variation,
    retries: int,
    delay_sec: float,
) -> cmapi.SimControlInteractive:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            sinfo = cmapi.ApoServerInfo(pid=carmaker.get_pid(), description="Idle")
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
    keep_movie_open: bool,
    keep_carmaker_open: bool,
) -> None:
    if simcontrol is not None:
        try:
            await simcontrol.disconnect()
        except Exception:
            pass

    if movie is not None and not keep_movie_open:
        try:
            await movie.stop()
        except Exception:
            pass

    if carmaker is not None and not keep_carmaker_open:
        try:
            await carmaker.stop()
        except Exception:
            pass


async def main() -> None:
    args = parse_args()
    project_root = args.project_root.resolve()
    cm_install = args.cm_install.resolve()
    testrun_rel_path = normalize_testrun_path(project_root, args.testrun)
    variation = load_variation(project_root, testrun_rel_path)

    carmaker: Optional[cmapi.CarMaker] = None
    movie: Optional[cmapi.IPGMovie] = None
    simcontrol: Optional[cmapi.SimControlInteractive] = None

    print(f"Project root: {project_root}")
    print(f"CarMaker install: {cm_install}")
    print(f"TestRun: Data/TestRun/{testrun_rel_path.as_posix()}")

    try:
        carmaker = await start_carmaker(cm_install, args.host)
        print(f"CarMaker started with PID {carmaker.get_pid()}")

        await asyncio.sleep(args.startup_settle_sec)

        if args.open_movie:
            movie = await start_movie(cm_install, args.host, carmaker)
            print(f"IPG-MOVIE started with PID {movie.get_pid()}")
            await asyncio.sleep(args.movie_settle_sec)

        simcontrol = await connect_simcontrol(
            carmaker,
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
            keep_movie_open=args.keep_movie_open,
            keep_carmaker_open=args.keep_carmaker_open,
        )


if __name__ == "__main__":
    cmapi.Task.run_main_task(main())