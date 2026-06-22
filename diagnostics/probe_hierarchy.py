"""Probe IPG-MOVIE window hierarchy."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

OUTPUT_DIR = Path(__file__).resolve().parent / "tmp" / "view_check"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

import win32ui  # noqa: F401
import dde  # type: ignore
from src.health.dde_health_check import render_dde_execute_script, run_check_attempt


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


# Probe window hierarchy
print("=== Window Hierarchy ===")
r = run_dde([
    'set ch [winfo children .]',
    'format "root_children=%s" $ch',
], "hierarchy1")
print(f"  {r.get('detail', '')[:500]}")

print("\n=== .f children ===")
r = run_dde([
    'set ch [winfo children .f]',
    'set sub [winfo children .f.tm]',
    'format ".f_children=%s .f.tm_children=%s" $ch $sub',
], "hierarchy2")
print(f"  {r.get('detail', '')[:500]}")

# Check for View array keys that might indicate view widgets
print("\n=== View Array Keys (first 40) ===")
r = run_dde([
    'set keys [array names View]',
    'set first40 [lrange $keys 0 39]',
    'format "count=%d keys=%s" [llength $keys] $first40',
], "view_keys")
print(f"  {r.get('detail', '')[:500]}")

# Try to find view window via different patterns
print("\n=== View Widget Search ===")
for pattern in ['.view', '.f.view', '.f.tm.view', '.f.tm.vp', '.vp', '.movie', '.f.tm.vp.view0', '.f.tm.vp.view']:
    r = run_dde([
        'catch {set w [winfo width ' + pattern + ']} we',
        'catch {set h [winfo height ' + pattern + ']} he',
        'catch {set ch2 [winfo children ' + pattern + ']} ch2e',
        'format "pattern=' + pattern + ' w=%s h=%s children=%s" $we $he $ch2e',
    ], "search_" + pattern.replace(".", "_"), timeout=5)
    detail = r.get("detail", "").strip()
    if "w=!" not in detail or "children=" in detail:
        print(f"  {detail[:300]}")
