from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dde_health_check import (
    DEFAULT_SERVICE,
    DEFAULT_TOPIC,
    classify_health_summary,
    default_output_dir,
    render_result_script,
    render_send_script,
    run_check_attempt,
)


PROCESS_ENUMERATION_COMMAND = r"""
$procs = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('HIL.exe', 'CM_Office.exe', 'CarMaker.win64.exe', 'Movie.exe', 'apobrokerd.exe') } |
    Select-Object ProcessId, Name, SessionId, CreationDate, CommandLine
if ($null -eq $procs) {
    '[]'
} else {
    @($procs) | ConvertTo-Json -Compress -Depth 4
}
""".strip()

RECENT_MOVIE_CRASHES_COMMAND = r"""
$events = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = (Get-Date).AddHours(-6) } -ErrorAction SilentlyContinue |
    Where-Object { $_.ProviderName -eq 'Application Error' -and $_.Message -match 'Movie\.exe' } |
    Select-Object -First 10 TimeCreated, Id, LevelDisplayName, ProviderName, Message
if ($null -eq $events) {
    '[]'
} else {
    @($events) | ConvertTo-Json -Compress -Depth 4
}
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll read-only IPG-MOVIE DDE health checks and capture the first unhealthy transition."
    )
    parser.add_argument("--service", default=DEFAULT_SERVICE, help="DDE service name for RunScript")
    parser.add_argument("--topic", default=DEFAULT_TOPIC, help="DDE topic name for RunScript")
    parser.add_argument("--attempts", type=int, default=1, help="Attempts per read-only check")
    parser.add_argument("--timeout-sec", type=float, default=2.0, help="Timeout per check attempt")
    parser.add_argument("--settle-sec", type=float, default=0.2, help="Delay between attempts inside one poll")
    parser.add_argument(
        "--poll-interval-sec",
        type=float,
        default=30.0,
        help="Delay between health polls",
    )
    parser.add_argument(
        "--max-polls",
        type=int,
        default=0,
        help="Maximum poll count; 0 means run until stopped or until first failure if enabled",
    )
    parser.add_argument(
        "--stop-on-first-failure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stop once the first unhealthy poll is captured",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory; defaults to SimOutput/ipgmovie_health_monitor/<timestamp>",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one poll and exit",
    )
    return parser.parse_args()


def default_monitor_output_dir() -> Path:
    return default_output_dir().parent.parent / "ipgmovie_health_monitor" / datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def _run_powershell_json(command: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    stdout = completed.stdout.strip()
    if not stdout:
        return []
    payload = json.loads(stdout)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    raise RuntimeError(f"Unexpected PowerShell JSON payload: {payload!r}")


def capture_process_snapshot() -> list[dict[str, Any]]:
    try:
        processes = _run_powershell_json(PROCESS_ENUMERATION_COMMAND)
    except Exception as exc:
        return [{"error": str(exc)}]
    processes.sort(key=lambda item: (str(item.get("Name") or ""), int(item.get("ProcessId") or 0)))
    return processes


def capture_gpu_snapshot() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=timestamp,name,driver_version,memory.used,memory.total,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except FileNotFoundError:
        return {"available": False, "error": "nvidia-smi not found"}
    except Exception as exc:
        return {"available": False, "error": str(exc)}

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    rows: List[dict[str, Any]] = []
    for line in lines:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 6:
            rows.append({"raw": line})
            continue
        rows.append(
            {
                "timestamp": parts[0],
                "name": parts[1],
                "driver_version": parts[2],
                "memory_used_mib": parts[3],
                "memory_total_mib": parts[4],
                "utilization_gpu_pct": parts[5],
            }
        )
    return {"available": True, "gpus": rows}


def capture_recent_movie_crashes() -> list[dict[str, Any]]:
    try:
        return _run_powershell_json(RECENT_MOVIE_CRASHES_COMMAND)
    except Exception as exc:
        return [{"error": str(exc)}]


def build_read_only_checks(output_dir: Path) -> list[tuple[str, str]]:
    return [
        (
            "tcleval_ping",
            render_result_script(
                output_dir / "tcleval_ping.txt",
                ['list ok [info patchlevel] [info nameofexecutable]'],
            ),
        ),
        (
            "interpreter_probe",
            render_result_script(
                output_dir / "interpreter_probe.txt",
                [
                    'set all [WInfoInterps "*"]',
                    'set movie [WInfoInterps "*MOVIE*"]',
                    'set exact [WInfoInterps "IPG-MOVIE"]',
                    'set gpu [WInfoInterps "GPUSensor_*"]',
                    'list all $all movie $movie exact $exact gpu $gpu',
                ],
            ),
        ),
        (
            "movie_ping",
            render_send_script(
                output_dir / "movie_ping.txt",
                "IPG-MOVIE",
                ['list ok [info patchlevel] camera $Camera::v(Name)'],
            ),
        ),
        (
            "movie_view_probe",
            render_send_script(
                output_dir / "movie_view_probe.txt",
                "IPG-MOVIE",
                [
                    'set vno $View(ev.view)',
                    'set wi [dict get $View($vno) Width]',
                    'set he [dict get $View($vno) Height]',
                    'list $wi $he',
                ],
            ),
        ),
        (
            "gpusensor_ping",
            render_send_script(
                output_dir / "gpusensor_ping.txt",
                "GPUSensor_1_0",
                ['list ok [info patchlevel]'],
            ),
        ),
    ]


def run_check(
    *,
    name: str,
    service: str,
    topic: str,
    output_dir: Path,
    script_text: str,
    attempts: int,
    timeout_sec: float,
    settle_sec: float,
) -> dict[str, Any]:
    records: List[dict[str, Any]] = []
    for attempt_index in range(max(1, attempts)):
        record = run_check_attempt(
            name=name,
            service=service,
            topic=topic,
            output_dir=output_dir,
            script_text=script_text,
            timeout_sec=max(0.1, timeout_sec),
        )
        record["attempt"] = attempt_index + 1
        record["attempts"] = max(1, attempts)
        records.append(record)
        status = "ok" if record.get("ok") else "failed"
        print(
            f"Monitor [{name}] attempt={attempt_index + 1}/{max(1, attempts)} "
            f"status={status} kind={record['kind']} elapsed_sec={record['elapsed_sec']:.3f}"
        )
        if record.get("ok"):
            break
        if attempt_index < max(1, attempts) - 1:
            time.sleep(max(settle_sec, 0.0) * (attempt_index + 1))

    return {
        "name": name,
        "ok": any(record.get("ok") for record in records),
        "attempts": records,
    }


def run_read_only_health_suite(
    *,
    service: str,
    topic: str,
    output_dir: Path,
    attempts: int,
    timeout_sec: float,
    settle_sec: float,
) -> dict[str, Any]:
    summary: Dict[str, Any] = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "service": service,
        "topic": topic,
        "attempts": attempts,
        "timeout_sec": timeout_sec,
        "settle_sec": settle_sec,
        "output_dir": str(output_dir),
        "checks": [],
    }
    for name, script_text in build_read_only_checks(output_dir):
        summary["checks"].append(
            run_check(
                name=name,
                service=service,
                topic=topic,
                output_dir=output_dir,
                script_text=script_text,
                attempts=attempts,
                timeout_sec=timeout_sec,
                settle_sec=settle_sec,
            )
        )
    summary["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    summary["all_ok"] = all(bool(check.get("ok")) for check in summary["checks"])
    summary["classification"] = classify_health_summary(summary)
    return summary


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def capture_poll_snapshot(
    *,
    poll_index: int,
    service: str,
    topic: str,
    root_output_dir: Path,
    attempts: int,
    timeout_sec: float,
    settle_sec: float,
    first_failure_seen: bool,
    saw_healthy: bool,
) -> dict[str, Any]:
    poll_dir = root_output_dir / f"poll_{poll_index:04d}"
    poll_dir.mkdir(parents=True, exist_ok=True)
    health = run_read_only_health_suite(
        service=service,
        topic=topic,
        output_dir=poll_dir,
        attempts=attempts,
        timeout_sec=timeout_sec,
        settle_sec=settle_sec,
    )
    payload: Dict[str, Any] = {
        "poll_index": poll_index,
        "polled_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "healthy": bool(health.get("all_ok")),
        "service": service,
        "topic": topic,
        "poll_dir": str(poll_dir),
        "health": health,
        "processes": capture_process_snapshot(),
    }

    classification = health.get("classification") or {}
    if not payload["healthy"] and not first_failure_seen:
        payload["first_failure"] = True
        payload["failure_after_healthy"] = bool(saw_healthy)
        payload["gpu"] = capture_gpu_snapshot()
        payload["recent_movie_crashes"] = capture_recent_movie_crashes()
        print(
            "Monitor first unhealthy poll: "
            f"poll={poll_index} code={classification.get('code')} after_healthy={bool(saw_healthy)}"
        )

    write_json(poll_dir / "summary.json", payload)
    return payload


def main() -> int:
    args = parse_args()
    root_output_dir = (args.output_dir or default_monitor_output_dir()).resolve()
    root_output_dir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, Any] = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "service": args.service,
        "topic": args.topic,
        "attempts": max(1, int(args.attempts)),
        "timeout_sec": max(0.1, float(args.timeout_sec)),
        "settle_sec": max(0.0, float(args.settle_sec)),
        "poll_interval_sec": 0.0 if args.once else max(0.1, float(args.poll_interval_sec)),
        "max_polls": int(args.max_polls),
        "stop_on_first_failure": bool(args.stop_on_first_failure),
        "output_dir": str(root_output_dir),
        "polls": [],
    }
    write_json(root_output_dir / "monitor_manifest.json", manifest)
    print(f"Monitor output dir: {root_output_dir}")

    poll_limit = 1 if args.once else int(args.max_polls)
    poll_index = 0
    saw_healthy = False
    first_failure_seen = False

    while True:
        poll_index += 1
        poll_payload = capture_poll_snapshot(
            poll_index=poll_index,
            service=args.service,
            topic=args.topic,
            root_output_dir=root_output_dir,
            attempts=max(1, int(args.attempts)),
            timeout_sec=max(0.1, float(args.timeout_sec)),
            settle_sec=max(0.0, float(args.settle_sec)),
            first_failure_seen=first_failure_seen,
            saw_healthy=saw_healthy,
        )
        manifest["polls"].append(
            {
                "poll_index": poll_payload["poll_index"],
                "polled_at": poll_payload["polled_at"],
                "healthy": poll_payload["healthy"],
                "poll_dir": poll_payload["poll_dir"],
                "classification": (poll_payload.get("health") or {}).get("classification"),
            }
        )
        if poll_payload["healthy"]:
            saw_healthy = True
        if poll_payload.get("first_failure"):
            first_failure_seen = True
            write_json(root_output_dir / "first_failure.json", poll_payload)

        write_json(root_output_dir / "latest.json", poll_payload)
        write_json(root_output_dir / "monitor_manifest.json", manifest)

        if args.once:
            break
        if poll_limit > 0 and poll_index >= poll_limit:
            break
        if first_failure_seen and args.stop_on_first_failure:
            break
        time.sleep(max(0.1, float(args.poll_interval_sec)))

    manifest["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest["completed_poll_count"] = poll_index
    manifest["saw_healthy"] = saw_healthy
    manifest["first_failure_seen"] = first_failure_seen
    write_json(root_output_dir / "monitor_manifest.json", manifest)
    return 2 if first_failure_seen else 0


if __name__ == "__main__":
    raise SystemExit(main())