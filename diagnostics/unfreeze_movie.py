"""Try to unfreeze IPG-MOVIE by resetting StopUpdateView and triggering UpdateView."""
import sys, os
sys.path.insert(0, '.')
from pathlib import Path
from health.dde_health_check import run_check_attempt, default_output_dir, render_dde_execute_script

output_dir = default_output_dir()
output_dir.mkdir(parents=True, exist_ok=True)
rf = output_dir / 'unfreeze_result.txt'
result_file = rf.as_posix()

# Step 1: Try to reset StopUpdateView and force an update
body = [
    f'set __fp [open "{result_file}" w]',
    # Check current SUV state
    'catch {set __suv_before [StopUpdateView]}',
    'puts $__fp "SUV_BEFORE:[expr {[info exists __suv_before] ? $__suv_before : {UNKNOWN}}]"',
    # Try to reset StopUpdateView to 0
    'catch {StopUpdateView 0}',
    # Force an update
    'catch {update idletasks}',
    # Try UpdateView
    'catch {UpdateView 0}',
    'catch {after 200}',
    # Check SUV state again
    'catch {set __suv_after [StopUpdateView]}',
    'puts $__fp "SUV_AFTER:[expr {[info exists __suv_after] ? $__suv_after : {UNKNOWN}}]"',
    # Check GL widget size after
    'catch {set __w [.view0.gl0 cget -width]}',
    'catch {set __h [.view0.gl0 cget -height]}',
    'puts $__fp "GL_AFTER:[expr {[info exists __w] ? $__w : {?}}]x[expr {[info exists __h] ? $__h : {?}}]"',
    'close $__fp',
]

print("Attempting to unfreeze IPG-MOVIE...")
result = run_check_attempt(
    name='unfreeze',
    service='TclEval', topic='CarMaker',
    output_dir=output_dir,
    script_text=render_dde_execute_script(rf, 'IPG-MOVIE', body),
    timeout_sec=20,
)
print(f"DDE ok: {result.get('ok')}")
print(f"Detail: {result.get('detail', '')}")

if rf.exists():
    content = rf.read_text().strip()
    print(f"\n=== Unfreeze Result ===")
    for line in content.split('\n'):
        print(f"  {line}")
else:
    print("Result file not found")
