from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

from health.dde_health_check import (
    DEFAULT_SERVICE,
    DEFAULT_TOPIC,
    LEGACY_SEND_CHAIN_NOTE,
    classify_health_summary,
    render_result_script,
    render_send_script,
    run_check_attempt,
    summarize_text,
)


PROCESS_SNAPSHOT_COMMAND = r"""
$procs = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('CarMaker.win64.exe', 'Movie.exe', 'apobrokerd.exe') } |
    Select-Object ProcessId, ParentProcessId, SessionId, CreationDate, Name, ExecutablePath, CommandLine
if ($null -eq $procs) {
    '[]'
} else {
    @($procs) | ConvertTo-Json -Compress
}
""".strip()


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a machine-readable snapshot of the retired IPG-MOVIE Tk send surface "
            "for explicit diagnostics only. This script is not part of the runtime calibration chain."
        )
    )
    parser.add_argument("--service", default=DEFAULT_SERVICE, help="DDE service name for RunScript")
    parser.add_argument("--topic", default=DEFAULT_TOPIC, help="DDE topic name for RunScript")
    parser.add_argument("--timeout-sec", type=float, default=2.0, help="Timeout per probe attempt")
    parser.add_argument("--attempts", type=int, default=1, help="Attempts per probe")
    parser.add_argument(
        "--settle-sec",
        type=float,
        default=0.2,
        help="Retry backoff base between probe attempts",
    )
    parser.add_argument(
        "--label",
        default="",
        help="Optional freeform label stored in the snapshot metadata",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory for generated Tcl scripts, raw results, and summary.json",
    )
    parser.add_argument(
        "--allow-legacy-send",
        action="store_true",
        help=(
            "Required opt-in. This script intentionally exercises the retired Tk send surface and must not "
            "be used as a runtime control path or fallback."
        ),
    )
    return parser.parse_args()


def default_output_dir() -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return Path(__file__).resolve().parents[3] / "SimOutput" / "send_surface_snapshot" / timestamp


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
    raise RuntimeError(f"Unexpected PowerShell JSON payload: {payload!r}")


def collect_processes() -> list[dict[str, Any]]:
    processes = _run_powershell_json(PROCESS_SNAPSHOT_COMMAND)
    for process in processes:
        process["kind"] = classify_process(process)
    return sorted(
        processes,
        key=lambda entry: (
            str(entry.get("Name") or ""),
            int(entry.get("ProcessId") or 0),
        ),
    )


def classify_process(process: dict[str, Any]) -> str:
    name = str(process.get("Name") or "")
    if name == "CarMaker.win64.exe":
        return "carmaker"
    if name == "apobrokerd.exe":
        return "apobrokerd"
    if name != "Movie.exe":
        return "other"

    command_line = str(process.get("CommandLine") or "").lower()
    if "-mode gpusensor" in command_line and "-headless" in command_line:
        return "gpusensor_movie"
    if "-cmgui" in command_line and "-apppid" in command_line:
        return "gui_movie_attached"
    if "-apppid" in command_line:
        return "gui_movie_detached_or_partial"
    return "movie_other"


def _read_window_text(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _read_class_name(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    buffer = ctypes.create_unicode_buffer(256)
    written = int(user32.GetClassNameW(hwnd, buffer, len(buffer)))
    if written <= 0:
        return ""
    return buffer.value


def collect_top_level_windows(target_pids: Set[int]) -> list[dict[str, Any]]:
    if not target_pids:
        return []

    user32 = ctypes.windll.user32
    records: list[dict[str, Any]] = []
    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd: int, lparam: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = int(pid.value)
        if process_id not in target_pids:
            return True

        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        records.append(
            {
                "hwnd": f"0x{int(hwnd):08X}",
                "pid": process_id,
                "title": _read_window_text(int(hwnd)),
                "class_name": _read_class_name(int(hwnd)),
                "visible": bool(user32.IsWindowVisible(hwnd)),
                "enabled": bool(user32.IsWindowEnabled(hwnd)),
                "rect": {
                    "left": int(rect.left),
                    "top": int(rect.top),
                    "right": int(rect.right),
                    "bottom": int(rect.bottom),
                    "width": int(rect.right - rect.left),
                    "height": int(rect.bottom - rect.top),
                },
            }
        )
        return True

    user32.EnumWindows(enum_proc_type(_callback), 0)
    return sorted(
        records,
        key=lambda entry: (
            int(entry.get("pid") or 0),
            0 if entry.get("visible") else 1,
            str(entry.get("title") or ""),
            str(entry.get("hwnd") or ""),
        ),
    )


def build_probes(output_dir: Path) -> list[tuple[str, str]]:
    return [
        (
            "tcleval_ping",
            render_result_script(
                output_dir / "tcleval_ping.txt",
                [
                    'list ok [info patchlevel]',
                ],
            ),
        ),
        (
            "dde_services_tcleval",
            render_result_script(
                output_dir / "dde_services_tcleval.txt",
                [
                    'package require dde',
                    'set services [dde services TclEval {}]',
                    'set lines {}',
                    'foreach service_entry $services {',
                    '    lassign $service_entry service_name service_topic',
                    '    lappend lines [list service $service_name topic $service_topic]',
                    '}',
                    'join $lines "\\n"',
                ],
            ),
        ),
        (
            "runtime_context",
            render_result_script(
                output_dir / "runtime_context.txt",
                [
                    'set lines {}',
                    'lappend lines [list pwd [pwd]]',
                    'if {[info exists TestRun(FName)]} {lappend lines [list testrun $TestRun(FName)]}',
                    'if {[catch {IFileRead TestRun "Vehicle"} vehicle_msg]} {',
                    '    lappend lines [list vehicle_error $vehicle_msg]',
                    '} else {',
                    '    lappend lines [list vehicle $vehicle_msg]',
                    '}',
                    'join $lines "\\n"',
                ],
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
            "movie_command_probe",
            render_result_script(
                output_dir / "movie_command_probe.txt",
                [
                    'set movie_cmds [info commands Movie]',
                    'set interps_before [WInfoInterps "*MOVIE*"]',
                    'set start_rc [catch {Movie start} start_msg]',
                    'set attach_rc [catch {Movie attach} attach_msg]',
                    'set interps_after [WInfoInterps "*MOVIE*"]',
                    'list movie_cmds $movie_cmds interps_before $interps_before start_rc $start_rc start_msg $start_msg attach_rc $attach_rc attach_msg $attach_msg interps_after $interps_after',
                ],
            ),
        ),
        (
            "movie_ping",
            render_send_script(
                output_dir / "movie_ping.txt",
                "IPG-MOVIE",
                [
                    'list ok [info patchlevel]',
                ],
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
                [
                    'list ok [info patchlevel]',
                ],
            ),
        ),
    ]


def run_probe(
    *,
    name: str,
    service: str,
    topic: str,
    output_dir: Path,
    script_text: str,
    attempts: int,
    timeout_sec: float,
    settle_sec: float,
) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    for attempt_index in range(max(attempts, 1)):
        record = run_check_attempt(
            name=name,
            service=service,
            topic=topic,
            output_dir=output_dir,
            script_text=script_text,
            timeout_sec=max(timeout_sec, 0.1),
        )
        record["attempt"] = attempt_index + 1
        record["attempts"] = max(attempts, 1)
        records.append(record)
        status = "ok" if record.get("ok") else "failed"
        print(
            f"Snapshot probe [{name}] attempt={attempt_index + 1}/{max(attempts, 1)} "
            f"status={status} kind={record['kind']} elapsed_sec={record['elapsed_sec']:.3f} "
            f"detail={summarize_text(record.get('detail', ''))}"
        )
        if record.get("ok"):
            break
        if attempt_index < max(attempts, 1) - 1:
            time.sleep(max(settle_sec, 0.0) * (attempt_index + 1))

    success = any(record.get("ok") for record in records)
    return {
        "name": name,
        "ok": success,
        "attempts": records,
        "first_success_attempt": next(
            (record["attempt"] for record in records if record.get("ok")),
            None,
        ),
    }


def build_summary(args: argparse.Namespace, output_dir: Path) -> Dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for name, script_text in build_probes(output_dir):
        checks.append(
            run_probe(
                name=name,
                service=args.service,
                topic=args.topic,
                output_dir=output_dir,
                script_text=script_text,
                attempts=args.attempts,
                timeout_sec=args.timeout_sec,
                settle_sec=args.settle_sec,
            )
        )

    core_check_names = {
        "tcleval_ping",
        "interpreter_probe",
        "movie_command_probe",
        "movie_ping",
        "movie_view_probe",
        "gpusensor_ping",
    }
    health_view = {
        "checks": [check for check in checks if check.get("name") in core_check_names],
    }
    health_view["all_ok"] = all(check.get("ok") for check in health_view["checks"])

    processes = collect_processes()
    pid_set = {int(entry.get("ProcessId") or 0) for entry in processes if int(entry.get("ProcessId") or 0) > 0}
    windows = collect_top_level_windows(pid_set)
    process_kind_counts: Dict[str, int] = {}
    for process in processes:
        kind = str(process.get("kind") or "other")
        process_kind_counts[kind] = process_kind_counts.get(kind, 0) + 1

    inferences: list[str] = []
    gui_movie_total = sum(
        count for kind, count in process_kind_counts.items() if kind.startswith("gui_movie")
    )
    if gui_movie_total > 1:
        inferences.append(
            f"Multiple GUI Movie processes are alive in the same session ({gui_movie_total})."
        )
    if process_kind_counts.get("gpusensor_movie", 0) > 0:
        inferences.append(
            f"Headless GPUSensor Movie processes present: {process_kind_counts['gpusensor_movie']}."
        )

    summary: Dict[str, Any] = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "label": args.label.strip() or None,
        "service": args.service,
        "topic": args.topic,
        "attempts": max(args.attempts, 1),
        "timeout_sec": max(args.timeout_sec, 0.1),
        "settle_sec": max(args.settle_sec, 0.0),
        "output_dir": str(output_dir),
        "checks": checks,
        "classification": classify_health_summary(health_view),
        "core_all_ok": health_view["all_ok"],
        "processes": processes,
        "process_kind_counts": process_kind_counts,
        "top_level_windows": windows,
        "inferences": inferences,
    }
    summary["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return summary


def main() -> int:
    args = parse_args()
    if not args.allow_legacy_send:
        raise SystemExit(
            "Refusing to run retired Tk send diagnostics without explicit opt-in. "
            "Re-run with --allow-legacy-send if you intentionally need a legacy snapshot."
        )

    output_dir = (args.output_dir or default_output_dir()).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Legacy send notice: {LEGACY_SEND_CHAIN_NOTE}")
    print(
        "Send-surface snapshot: "
        f"service={args.service}, topic={args.topic}, attempts={max(args.attempts, 1)}, "
        f"timeout_sec={max(args.timeout_sec, 0.1)}, output_dir={output_dir}"
    )
    summary = build_summary(args, output_dir)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    classification = summary.get("classification") or {}
    print(f"Send-surface snapshot summary: {summary_path}")
    print(
        "Send-surface classification: "
        f"{classification.get('code', 'unknown')} | {classification.get('message', '')}"
    )
    print(
        "Captured runtime surfaces: "
        f"processes={len(summary.get('processes', []))}, "
        f"windows={len(summary.get('top_level_windows', []))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())