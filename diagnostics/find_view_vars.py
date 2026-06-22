"""Find correct IPG-MOVIE view variable names."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from pathlib import Path

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


# Find view-related variables
print("=== Discovering View Variables ===")
r = run_dde([
    'set all [array names View]',
    'set trimmed [lrange $all 0 19]',
    'format "total=%d first20=%s" [llength $all] $trimmed',
], "find_view_vars")
print(f"  {r.get('detail', '')[:800]}")

# Try common view variable patterns - use format to return results
print("\n=== Trying View Variable Access ===")
for expr, desc in [
    ('info exists View(ev.view)', 'ev.view_exists'),
    ('array names View', 'array_names'),
    ('catch {set v $View(ev.view)}', 'ev.view'),
    ('catch {set v $View(0)}', 'View_0'),
    ('info exists ::View', 'global_View'),
    ('winfo children .', 'children'),
    ('winfo exists .view0', 'view0_exists'),
    ('catch {winfo width .view0}', 'view0_width'),
    ('catch {winfo height .view0}', 'view0_height'),
]:
    r = run_dde([f'format "{desc}=%s" [catch {{{expr}}} val];format "{desc}=%s" $val'], desc, timeout=5)
    detail = r.get("detail", "").strip()
    print(f"  {detail[:300]}")
