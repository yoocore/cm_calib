from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_SERVICE = "TclEval"
DEFAULT_TOPIC = "CarMaker"
LEGACY_SEND_CHAIN_NOTE = (
    "Legacy diagnostic only: Tk send probes are retained for explicit fault snapshots. "
    "Do not use them in runtime calibration, recovery, or fallback paths."
)


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
    channel_var = f"__copilot_out_{uuid.uuid4().hex}"
    lines = [
        f'set {channel_var} [open "{result_path.as_posix()}" w]',
        "set rc [catch {",
    ]
    lines.extend(f"    {line}" for line in body_lines)
    lines.extend(
        [
            "} msg]",
            f'puts ${channel_var} "rc=$rc"',
            f'puts ${channel_var} "msg_begin"',
            f"puts ${channel_var} $msg",
            f'puts ${channel_var} "msg_end"',
            f"close ${channel_var}",
            "",
        ]
    )
    return "\n".join(lines)


def render_send_script(result_path: Path, target_appname: str, body_lines: List[str]) -> str:
    """Render a legacy Tk send probe for explicit diagnostics only."""

    channel_var = f"__copilot_out_{uuid.uuid4().hex}"
    lines = [
        f'set {channel_var} [open "{result_path.as_posix()}" w]',
        f"set rc [catch {{send {target_appname} {{",
    ]
    lines.extend(f"    {line}" for line in body_lines)
    lines.extend(
        [
            "}} msg]",
            f'puts ${channel_var} "rc=$rc"',
            f'puts ${channel_var} "msg_begin"',
            f"puts ${channel_var} $msg",
            f'puts ${channel_var} "msg_end"',
            f"close ${channel_var}",
            "",
        ]
    )
    return "\n".join(lines)


def render_dde_execute_script(
    result_path: Path,
    target_topic: str,
    body_lines: List[str],
    *,
    target_service: str = "TclEval",
) -> str:
    channel_var = f"__copilot_out_{uuid.uuid4().hex}"
    remote_result_path = result_path.with_suffix(f"{result_path.suffix or '.txt'}.remote")
    lines = [
        f'set {channel_var} [open "{result_path.as_posix()}" w]',
        f'set __copilot_remote_result_path "{remote_result_path.as_posix()}"',
        'catch {file delete -force $__copilot_remote_result_path}',
        "set rc [catch {",
        "    package require dde",
        f"    dde execute {target_service} {target_topic} {{",
        '        set __copilot_remote_out [open "' + remote_result_path.as_posix() + '" w]',
        '        set __copilot_remote_rc [catch {',
    ]
    lines.extend(f"            {line}" for line in body_lines)
    lines.extend(
        [
            '        } __copilot_remote_msg]',
            '        puts $__copilot_remote_out "rc=$__copilot_remote_rc"',
            '        puts $__copilot_remote_out "msg_begin"',
            '        puts $__copilot_remote_out $__copilot_remote_msg',
            '        puts $__copilot_remote_out "msg_end"',
            '        close $__copilot_remote_out',
            '        if {$__copilot_remote_rc != 0} {error $__copilot_remote_msg}',
            "    }",
            "} msg]",
            'set __copilot_remote_wait_deadline [expr {[clock milliseconds] + 1000}]',
            'while {![file exists $__copilot_remote_result_path] && [clock milliseconds] < $__copilot_remote_wait_deadline} {',
            '    after 25',
            '}',
            'if {[file exists $__copilot_remote_result_path]} {',
            f'    set __copilot_remote_in [open $__copilot_remote_result_path r]',
            '    set __copilot_remote_payload [read $__copilot_remote_in]',
            '    close $__copilot_remote_in',
            f'    puts -nonewline ${channel_var} $__copilot_remote_payload',
            '    catch {file delete -force $__copilot_remote_result_path}',
            '} else {',
            f'    puts ${channel_var} "rc=$rc"',
            f'    puts ${channel_var} "msg_begin"',
            f"    puts ${channel_var} $msg",
            f'    puts ${channel_var} "msg_end"',
            '}',
            f"close ${channel_var}",
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
    runscript_error_detail: Optional[str] = None
    try:
        run_runscript(service, topic, script_path)
    except Exception as exc:
        runscript_error_detail = str(exc)

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
                    "runscript_error_detail": runscript_error_detail,
                    "script_path": str(script_path),
                    "result_path": str(result_path),
                }
        time.sleep(0.05)

    elapsed = time.perf_counter() - started
    if runscript_error_detail is not None:
        return {
            "ok": False,
            "kind": "runscript_error",
            "elapsed_sec": elapsed,
            "detail": runscript_error_detail,
            "script_path": str(script_path),
            "result_path": str(result_path),
        }
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
            "interpreter_probe",
            render_result_script(
                output_dir / "interpreter_probe.txt",
                [
                    'set all [WInfoInterps "*"]',
                    'set movie [WInfoInterps "*MOVIE*"]',
                    'set exact [WInfoInterps "IPG-MOVIE"]',
                    'list all $all movie $movie exact $exact',
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
                    'set interps_after [WInfoInterps "*MOVIE*"]',
                    'list movie_cmds $movie_cmds interps_before $interps_before interps_after $interps_after',
                ],
            ),
        ),
        (
            "movie_ping",
            render_dde_execute_script(
                output_dir / "movie_ping.txt",
                "IPG-MOVIE",
                [
                    'list ok [info patchlevel]',
                ],
            ),
        ),
        (
            "movie_camera_probe",
            render_dde_execute_script(
                output_dir / "movie_camera_probe.txt",
                "IPG-MOVIE",
                [
                    'if {![info exists Camera::v(Name)]} { error "Camera::v(Name) missing" }',
                    'list camera $Camera::v(Name)',
                ],
            ),
        ),
        (
            "movie_view_probe",
            render_dde_execute_script(
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
            render_dde_execute_script(
                output_dir / "gpusensor_ping.txt",
                "GPUSensor_1_0",
                [
                    'list ok [info patchlevel]',
                ],
            ),
        ),
        (
            "movie_render_probe",
            render_dde_execute_script(
                output_dir / "movie_render_probe.txt",
                "IPG-MOVIE",
                [
                    'set uva $::View(UpdateViewActive)',
                    'set suv $::View(StopUpdateView)',
                    'set uc $::View(UpdateCounter)',
                    '# UVA==1 check removed: TimerProc sets UVA=1 only briefly during frame',
                    '# rendering; between frames UVA=0 even when healthy.',
                    'if {$suv ne "0"} { error "StopUpdateView=$suv (expected 0)" }',
                    'if {$uc eq "" || $uc < 0} { error "Invalid UpdateCounter=$uc" }',
                    'list UpdateViewActive $uva StopUpdateView $suv UpdateCounter $uc',
                ],
            ),
        ),
        (
            "movie_fbo_probe",
            render_dde_execute_script(
                output_dir / "movie_fbo_probe.txt",
                "IPG-MOVIE",
                [
                    "set vno \$::View(ev.view)",
                    "set w [dict get \$::View(\$vno) Widget]",
                    "if {[catch {\$w image extract -width 1 -height 1 -format RGBA -data {}} err]} {",
                    "    error {FBO probe failed: $err}",
                    "}",
                    "list ok {FBO probe passed}",
                ],
            ),
        ),

    ]


def _get_check(summary: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for check in summary.get("checks", []):
        if check.get("name") == name:
            return check
    return None


def _get_check_detail(check: Optional[Dict[str, Any]]) -> str:
    if not isinstance(check, dict):
        return ""
    for attempt in check.get("attempts", []):
        if attempt.get("ok"):
            return str(attempt.get("detail", "")).strip()
    attempts = check.get("attempts", [])
    if attempts:
        return str(attempts[-1].get("detail", "")).strip()
    return ""


def classify_health_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    tcleval_ping = _get_check(summary, "tcleval_ping")
    interpreter_probe = _get_check(summary, "interpreter_probe")
    movie_command_probe = _get_check(summary, "movie_command_probe")
    movie_ping = _get_check(summary, "movie_ping")
    movie_camera_probe = _get_check(summary, "movie_camera_probe")
    movie_view_probe = _get_check(summary, "movie_view_probe")
    gpusensor_ping = _get_check(summary, "gpusensor_ping")

    interpreter_detail = _get_check_detail(interpreter_probe)
    movie_command_detail = _get_check_detail(movie_command_probe)
    exact_registered = bool(re.search(r"\bexact\b\s+\{?IPG-MOVIE\}?", interpreter_detail))
    gpu_registered = "GPUSensor_1_0" in interpreter_detail
    movie_command_ok = isinstance(movie_command_probe, dict) and bool(movie_command_probe.get("ok"))
    gpu_ping_ok = isinstance(gpusensor_ping, dict) and bool(gpusensor_ping.get("ok"))
    movie_ping_ok = isinstance(movie_ping, dict) and bool(movie_ping.get("ok"))
    movie_camera_ok = None
    if isinstance(movie_camera_probe, dict):
        movie_camera_ok = bool(movie_camera_probe.get("ok"))
    movie_view_ok = isinstance(movie_view_probe, dict) and bool(movie_view_probe.get("ok"))
    movie_render_probe = _get_check(summary, "movie_render_probe")
    movie_render_ok = None
    if isinstance(movie_render_probe, dict):
        movie_render_ok = bool(movie_render_probe.get("ok"))
    movie_fbo_probe = _get_check(summary, "movie_fbo_probe")
    movie_fbo_ok = None
    if isinstance(movie_fbo_probe, dict):
        movie_fbo_ok = bool(movie_fbo_probe.get("ok"))

    target_status = {
        "movie_command_ok": movie_command_ok,
        "ipg_movie_registered": exact_registered,
        "ipg_movie_send_ok": movie_ping_ok,
        "ipg_movie_camera_probe_ok": movie_camera_ok,
        "ipg_movie_view_probe_ok": movie_view_ok,
        "ipg_movie_render_ok": movie_render_ok,
        "gpusensor_registered": gpu_registered,
        "gpusensor_send_ok": gpu_ping_ok,
        "ipg_movie_fbo_ok": movie_fbo_ok,
    }

    if bool(summary.get("all_ok")):
        return {
            "code": "ok",
            "message": "TclEval and dde execute remote-control checks all passed.",
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": exact_registered,
            "target_status": target_status,
        }

    if movie_ping_ok and movie_camera_ok is False and movie_view_ok and gpu_registered and gpu_ping_ok:
        return {
            "code": "movie_camera_surface_unstable",
            "message": (
                "Minimal dde execute control to IPG-MOVIE still works, the active view probe still works, and GPUSensor_1_0 still responds, "
                "but Camera::v(Name) access fails. This narrows the onset to the GUI Movie camera state/namespace rather than "
                "a whole remote-control-surface failure."
            ),
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": exact_registered,
            "target_status": target_status,
        }

    if movie_ping_ok and movie_camera_ok is False:
        return {
            "code": "movie_camera_probe_failed",
            "message": (
                "Minimal dde execute control to IPG-MOVIE still works, but the Camera namespace probe does not. "
                "This points to a Movie-side camera-state failure rather than a missing remote-control path."
            ),
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": exact_registered,
            "target_status": target_status,
        }

    if not (isinstance(tcleval_ping, dict) and tcleval_ping.get("ok")):
        return {
            "code": "tcleval_unavailable",
            "message": "CarMaker RunScript/TclEval is unavailable, so Movie remote-control health cannot be verified.",
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": exact_registered,
            "target_status": target_status,
        }

    if movie_command_ok and exact_registered and not movie_ping_ok and gpu_registered and not gpu_ping_ok:
        return {
            "code": "movie_commands_alive_but_tk_send_surface_failed",
            "message": (
                "CarMaker-side Movie command surface is still present, but dde execute control to both IPG-MOVIE and GPUSensor_1_0 is rejected. "
                "This isolates the fault to the Movie-side remote execution surface rather than TclEval itself."
            ),
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": True,
            "target_status": target_status,
        }

    if exact_registered and not movie_ping_ok and gpu_registered and gpu_ping_ok:
        return {
            "code": "ipg_movie_target_only_unresponsive",
            "message": (
                "CarMaker can control GPUSensor_1_0 via dde execute but not IPG-MOVIE. The failure is isolated to the "
                "GUI Movie interpreter/registration rather than the whole remote execution surface."
            ),
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": True,
            "target_status": target_status,
        }

    if exact_registered and not movie_ping_ok and gpu_registered and not gpu_ping_ok:
        return {
            "code": "movie_send_targets_unresponsive",
            "message": (
                "CarMaker resolves both IPG-MOVIE and GPUSensor_1_0, but dde execute control fails for both targets. "
                "This points to a broader Movie-side remote execution failure, not just the GUI window."
            ),
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": True,
            "target_status": target_status,
        }

    if exact_registered and not movie_ping_ok:
        return {
            "code": "movie_send_target_registered_but_unresponsive",
            "message": (
                "CarMaker can still resolve appname IPG-MOVIE via WInfoInterps, but even minimal "
                "dde execute commands are rejected. This points to a wedged Movie target/registration state, "
                "not a missing Movie process."
            ),
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": True,
            "target_status": target_status,
        }

    if not exact_registered and not movie_ping_ok:
        return {
            "code": "movie_interpreter_missing",
            "message": "CarMaker cannot resolve appname IPG-MOVIE via WInfoInterps.",
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": False,
            "target_status": target_status,
        }

    if not movie_view_ok:
        return {
            "code": "movie_view_probe_failed",
            "message": "dde execute control partially works, but the active Movie view probe did not succeed.",
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": exact_registered,
            "target_status": target_status,
        }

    if movie_render_ok is False:
        return {
            "code": "movie_render_probe_failed",
            "message": (
                "DDE execute and view probe work, but movie_render_probe returned an error. "
                "This is unexpected; check individual check attempts for details."
            ),
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": exact_registered,
            "target_status": target_status,
        }

    if movie_render_ok is None:
        return {
            "code": "movie_render_not_checked",
            "message": (
                "DDE execute and view probe work, but rendering state could not be probed "
                "(movie_render_probe check unavailable or returned no data)."
            ),
            "interpreter_probe": interpreter_detail or None,
            "movie_command_probe": movie_command_detail or None,
            "exact_ipg_movie_registered": exact_registered,
            "target_status": target_status,
        }

    return {
        "code": "partial_failure",
        "message": "DDE health checks are partially failing; inspect individual check attempts.",
        "interpreter_probe": interpreter_detail or None,
        "movie_command_probe": movie_command_detail or None,
        "exact_ipg_movie_registered": exact_registered,
        "target_status": target_status,
    }


def run_health_suite(
    *,
    service: str,
    topic: str,
    output_dir: Path,
    attempts: int,
    timeout_sec: float,
    settle_sec: float,
) -> Dict[str, Any]:
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

    print(
        "DDE health check: "
        f"service={service}, topic={topic}, attempts={attempts}, "
        f"timeout_sec={timeout_sec}, output_dir={output_dir}"
    )

    for name, script_text in build_checks(output_dir):
        summary["checks"].append(
            run_check(
                name=name,
                service=service,
                topic=topic,
                output_dir=output_dir,
                script_text=script_text,
                attempts=max(attempts, 1),
                timeout_sec=max(timeout_sec, 0.1),
                settle_sec=max(settle_sec, 0.0),
            )
        )

    summary["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    summary["all_ok"] = all(check.get("ok") for check in summary["checks"])
    summary["classification"] = classify_health_summary(summary)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def run_read_only_health_suite(
    *,
    service: str,
    topic: str,
    output_dir: Path,
    attempts: int,
    timeout_sec: float,
    settle_sec: float,
) -> Dict[str, Any]:
    return run_health_suite(
        service=service,
        topic=topic,
        output_dir=output_dir,
        attempts=attempts,
        timeout_sec=timeout_sec,
        settle_sec=settle_sec,
    )


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = run_health_suite(
        service=args.service,
        topic=args.topic,
        output_dir=output_dir,
        attempts=max(args.attempts, 1),
        timeout_sec=max(args.timeout_sec, 0.1),
        settle_sec=max(args.settle_sec, 0.0),
    )

    print(f"DDE health summary: {summary['summary_path']}")
    classification = summary.get("classification") or {}
    print(
        "DDE health classification: "
        f"{classification.get('code', 'unknown')} | {classification.get('message', '')}"
    )
    print(f"DDE health all_ok: {summary['all_ok']}")
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())