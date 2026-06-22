"""Diagnose IPG-MOVIE state when window appears frozen."""
import sys, os
sys.path.insert(0, '.')
from pathlib import Path
from health.dde_health_check import run_check_attempt, default_output_dir, render_dde_execute_script

output_dir = default_output_dir()
output_dir.mkdir(parents=True, exist_ok=True)
rf = output_dir / 'diag_state.txt'
result_file = rf.as_posix()

body = [
    f'set __fp [open "{result_file}" w]',
    'puts $__fp "VIEW0:[winfo exists .view0]"',
    'puts $__fp "CHECKVP:[info commands CheckViewPort]"',
    'puts $__fp "CHECKVP_SAVED:[info commands CheckViewPort_saved]"',
    'puts $__fp "WM_STATE:[wm state .]"',
    'puts $__fp "VIEW_EV_VIEW:[expr {[info exists View(ev.view)] ? $View(ev.view) : {MISSING}}]"',
    'catch {set __suva [StopUpdateView]}',
    'puts $__fp "STOP_UPDATE_VIEW:[expr {[info exists __suva] ? $__suva : {UNKNOWN}}]"',
    'catch {set __gl_w [.view0.gl0 cget -width]}',
    'catch {set __gl_h [.view0.gl0 cget -height]}',
    'puts $__fp "GL_WIDGET:[expr {[info exists __gl_w] ? $__gl_w : {?}}]x[expr {[info exists __gl_h] ? $__gl_h : {?}}]"',
    'if {[info exists View(0)]} {',
    '    set __dw [dict get $View(0) Width]',
    '    set __dh [dict get $View(0) Height]',
    '    puts $__fp "VIEW_DICT:${__dw}x${__dh}"',
    '} else {',
    '    puts $__fp "VIEW_DICT:MISSING"',
    '}',
    'puts $__fp "ABRAXAS:[expr {[info exists View(ABRAXAS)] ? $View(ABRAXAS) : {MISSING}}]"',
    'catch {puts $__fp "CAMERA_NAME:[expr {[info exists Camera::v(Name)] ? $Camera::v(Name) : {NONE}}]"}',
    'close $__fp',
]

print("Sending DDE diagnosis...")
result = run_check_attempt(
    name='diag_state',
    service='TclEval', topic='CarMaker',
    output_dir=output_dir,
    script_text=render_dde_execute_script(rf, 'IPG-MOVIE', body),
    timeout_sec=15,
)
print(f"DDE ok: {result.get('ok')}")
print(f"Detail: {result.get('detail', '')}")

if rf.exists():
    content = rf.read_text().strip()
    print(f"\n=== IPG-MOVIE State ===")
    for line in content.split('\n'):
        print(f"  {line}")
else:
    print("Result file not found - IPG-MOVIE may be completely unresponsive")
