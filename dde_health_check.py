from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_SERVICE = "TclEval"
DEFAULT_TOPIC = "CarMaker"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run minimal DDE health checks against CarMaker/IPG-MOVIE without starting calibration."
    )
    parser.add_argument("--service", default=DEFAULT_SERVICE, help="DDE service name for RunScript")
    parser.add_argument("--topic", default=DEFAULT_TOPIC, help="DDE topic name for RunScript")
    parser.add_argument("--attempts", type=int, default=3, help="Attempts per check")
    parser.add_argument("--timeout-sec", type=float, default=2.0, help="Timeout per check attempt")
    parser.add_argument(
        "--settle-sec",
        type=float,
        default=0.2,
        help="Base retry delay between attempts",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory for generated scripts and results",
    )
    return parser.parse_args()


def default_output_dir() -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return Path(__file__).resolve().parents[3] / "SimOutput" / "dde_health_check" / timestamp


def summarize_text(value: object, limit: int = 240) -> str:
    text = str(value).replace("\r", " ").replace("\n", " | ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def parse_result_text(text: str) -> Tuple[int, str]:
    lines = [line.rstrip("\r") for line in text.splitlines()]
    rc_line = next((line for line in lines if line.startswith("rc=")), None)
    if rc_line is None:
        raise RuntimeError(f"Result is missing rc=: {text!r}")

    try:
        rc = int(rc_line.split("=", 1)[1].strip())
    except ValueError as exc:
        raise RuntimeError(f"Invalid rc line: {rc_line!r}") from exc

    if "msg_begin" in lines and "msg_end" in lines:
        start = lines.index("msg_begin") + 1
        end = lines.index("msg_end")
        msg = "\n".join(lines[start:end]).strip()
    else:
        msg = "\n".join(line for line in lines if not line.startswith("rc=")).strip()
    return rc, msg


def is_result_complete(text: str) -> bool:
    stripped = text.strip()
    if not stripped or "rc=" not in stripped:
        return False
    if "msg_begin" in stripped:
        return "msg_end" in stripped
    return True


def render_result_script(result_path: Path, body_lines: List[str]) -> str:
    lines = [
        f'set out [open "{result_path.as_posix()}" w]',
        "proc emit {text} {",
        "    global out",
        "    puts $out $text",
        "}",
        "set rc [catch {",
    ]
    lines.extend(f"    {line}" for line in body_lines)
    lines.extend(
        [
            "} msg]",
            'emit "rc=$rc"',
            'emit "msg_begin"',
            "emit $msg",
            'emit "msg_end"',
            "close $out",
            "",
        ]
    )
    return "\n".join(lines)


def render_movie_send_script(result_path: Path, movie_body_lines: List[str]) -> str:
    lines = [
        f'set out [open "{result_path.as_posix()}" w]',
        "proc emit {text} {",
        "    global out",
        "    puts $out $text",
        "}",
        "set rc [catch {send IPG-MOVIE {",
    ]
    lines.extend(f"    {line}" for line in movie_body_lines)
    lines.extend(
        [
            "}} msg]",
            'emit "rc=$rc"',
            'emit "msg_begin"',
            "emit $msg",
            'emit "msg_end"',
            "close $out",
            "",
        ]
    )
    return "\n".join(lines)


def run_runscript(service: str, topic: str, script_path: Path) -> None:
    try:
        import win32ui  # noqa: F401
        import dde  # type: ignore
    except Exception as exc:
        raise RuntimeError("pywin32 DDE support is unavailable") from exc

    server = None
    try:
        server = dde.CreateServer()
        server.Create(f"CopilotDDEHealth.{uuid.uuid4().hex}")
        conv = dde.CreateConversation(server)
        conv.ConnectTo(service, topic)
        conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
    except Exception as exc:
        raise RuntimeError(f"RunScript failed: service={service}, topic={topic}, error={exc}") from exc
    finally:
        if server is not None:
            try:
                server.Shutdown()
            except Exception:
                pass


def run_check_attempt(
    name: str,
    service: str,
    topic: str,
    output_dir: Path,
    script_text: str,
    timeout_sec: float,
) -> Dict[str, Any]:
    script_path = output_dir / f"{name}.tcl"
    result_path = output_dir / f"{name}.txt"
    script_path.write_text(script_text, encoding="utf-8")
    try:
        result_path.unlink()
    except FileNotFoundError:
        pass

    started = time.perf_counter()
    try:
        run_runscript(service, topic, script_path)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            "ok": False,
            "kind": "runscript_error",
            "elapsed_sec": elapsed,
            "detail": str(exc),
            "script_path": str(script_path),
            "result_path": str(result_path),
        }

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if result_path.exists():
            text = result_path.read_text(encoding="utf-8", errors="replace")
            if is_result_complete(text):
                elapsed = time.perf_counter() - started
                try:
                    rc, msg = parse_result_text(text)
                except Exception as exc:
                    return {
                        "ok": False,
                        "kind": "parse_error",
                        "elapsed_sec": elapsed,
                        "detail": str(exc),
                        "raw_text": text,
                        "script_path": str(script_path),
                        "result_path": str(result_path),
                    }
                return {
                    "ok": rc == 0,
                    "kind": "result_ok" if rc == 0 else "result_error",
                    "elapsed_sec": elapsed,
                    "detail": msg,
                    "rc": rc,
                    "script_path": str(script_path),
                    "result_path": str(result_path),
                }
        time.sleep(0.05)

    elapsed = time.perf_counter() - started
    return {
        "ok": False,
        "kind": "timeout",
        "elapsed_sec": elapsed,
        "detail": f"Timed out waiting for {result_path.name}",
        "script_path": str(script_path),
        "result_path": str(result_path),
    }


def run_check(
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
    for attempt_index in range(attempts):
        record = run_check_attempt(name, service, topic, output_dir, script_text, timeout_sec)
        record["attempt"] = attempt_index + 1
        record["attempts"] = attempts
        records.append(record)
        status = "ok" if record.get("ok") else "failed"
        print(
            f"DDE health [{name}] attempt={attempt_index + 1}/{attempts} "
            f"status={status} kind={record['kind']} elapsed_sec={record['elapsed_sec']:.3f} "
            f"detail={summarize_text(record.get('detail', ''))}"
        )
        if record.get("ok"):
            break
        if attempt_index < attempts - 1:
            time.sleep(max(settle_sec, 0.2) * (attempt_index + 1))

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


def build_checks(output_dir: Path) -> List[Tuple[str, str]]:
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
            "movie_ping",
            render_movie_send_script(
                output_dir / "movie_ping.txt",
                [
                    'list ok [info patchlevel]',
                ],
            ),
        ),
        (
            "movie_view_probe",
            render_movie_send_script(
                output_dir / "movie_view_probe.txt",
                [
                    'set vno $View(ev.view)',
                    'set wi [dict get $View($vno) Width]',
                    'set he [dict get $View($vno) Height]',
                    'list $wi $he',
                ],
            ),
        ),
    ]


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "service": args.service,
        "topic": args.topic,
        "attempts": args.attempts,
        "timeout_sec": args.timeout_sec,
        "settle_sec": args.settle_sec,
        "output_dir": str(output_dir),
        "checks": [],
    }

    print(
        "DDE health check: "
        f"service={args.service}, topic={args.topic}, attempts={args.attempts}, "
        f"timeout_sec={args.timeout_sec}, output_dir={output_dir}"
    )

    for name, script_text in build_checks(output_dir):
        summary["checks"].append(
            run_check(
                name=name,
                service=args.service,
                topic=args.topic,
                output_dir=output_dir,
                script_text=script_text,
                attempts=max(args.attempts, 1),
                timeout_sec=max(args.timeout_sec, 0.1),
                settle_sec=max(args.settle_sec, 0.0),
            )
        )

    summary["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    summary["all_ok"] = all(check.get("ok") for check in summary["checks"])
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"DDE health summary: {summary_path}")
    print(f"DDE health all_ok: {summary['all_ok']}")
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())