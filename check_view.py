"""Check IPG-MOVIE view via send command through CarMaker."""
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "tmp" / "view_check"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

import win32ui  # noqa: F401
import dde  # type: ignore
from dde_health_check import render_dde_execute_script, run_check_attempt


def run_dde(body_lines, name, timeout=10):
    result_path = OUTPUT_DIR / f"{name}.txt"
    script_text = render_dde_execute_script(result_path, "CarMaker", body_lines, target_service="TclEval")
    try:
        result_path.unlink()
    except FileNotFoundError:
        pass
    return run_check_attempt(
        name=name, service="TclEval", topic="CarMaker",
        output_dir=OUTPUT_DIR, script_text=script_text, timeout_sec=timeout,
    )


# Try send to IPG-MOVIE for view info
print("=== View via send IPG-MOVIE ===")
r = run_dde([
    'set rc [catch {send IPG-MOVIE {array names View}} msg]',
    'format "rc=%d msg=%s" $rc $msg',
], "send_test")
print(f"  ok={r.get('ok')} detail={r.get('detail', '')[:500]}")

# Try WInfoInterps
print("\n=== WInfoInterps ===")
r = run_dde([
    'set interps [WInfoInterps]',
    'format "interps=%s" $interps',
], "winfo_interps")
print(f"  ok={r.get('ok')} detail={r.get('detail', '')[:500]}")

# Try send to IPG-MOVIE for View(ev.view)
print("\n=== View(ev.view) via send ===")
r = run_dde([
    'set rc [catch {send IPG-MOVIE {set View(ev.view)}} msg]',
    'format "rc=%d view_ev=%s" $rc $msg',
], "send_view_ev")
print(f"  ok={r.get('ok')} detail={r.get('detail', '')[:500]}")

# Try wm geometry of IPG-MOVIE
print("\n=== Movie geometry via send ===")
r = run_dde([
    'set rc [catch {send IPG-MOVIE {wm geometry .}} msg]',
    'format "rc=%d geom=%s" $rc $msg',
], "send_geom")
print(f"  ok={r.get('ok')} detail={r.get('detail', '')[:500]}")
