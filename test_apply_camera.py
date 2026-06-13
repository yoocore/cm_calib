"""
Standalone test: apply right_rear camera parameters via DDE and check if 3D view changes.
Usage: python test_apply_camera.py
Prerequisites: cm prepare already done, CarMaker 3D view visible.
"""
import sys, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dde_health_check import render_dde_execute_script, run_runscript

# right_rear parameters (same as calibration uses)
PARAMS = {
    "pos_x": 3.4413,
    "pos_y": -0.9512,
    "pos_z": 0.9608,
    "roll": 0.3714,
    "pitch": -1.0052,
    "yaw": 227.8997,
    "lens_fov": 124.7,
    "lens_scale": 1.0,
    "lens_offset_x": 0.0,
    "lens_offset_y": 0.0,
}

WRITE_WIDGETS = {
    "roll": ".camera.presetFrame.x",
    "pitch": ".camera.presetFrame.y",
    "yaw": ".camera.presetFrame.z",
    "pos_x": ".camera.presetFrame.evptx",
    "pos_y": ".camera.presetFrame.evpty",
    "pos_z": ".camera.presetFrame.evptz",
    "lens_fov": ".camera.cammoddlg.fov.e",
    "lens_scale": ".camera.cammoddlg.fisheye.ctrl.e1",
    "lens_offset_x": ".camera.cammoddlg.fisheye.ctrl.e2",
    "lens_offset_y": ".camera.cammoddlg.fisheye.ctrl.e3",
}

READ_WIDGETS = {
    "pos_x": ".camera.presetFrame.svptx",
    "pos_y": ".camera.presetFrame.svpty",
    "pos_z": ".camera.presetFrame.svptz",
    "roll": ".camera.presetFrame.x",
    "pitch": ".camera.presetFrame.y",
    "yaw": ".camera.presetFrame.z",
    "lens_fov": ".camera.cammoddlg.fov.e",
    "lens_scale": ".camera.cammoddlg.fisheye.ctrl.e1",
    "lens_offset_x": ".camera.cammoddlg.fisheye.ctrl.e2",
    "lens_offset_y": ".camera.cammoddlg.fisheye.ctrl.e3",
}


def main():
    out_dir = Path(r"E:\Temp\opencode")
    out_dir.mkdir(exist_ok=True)
    result_path = out_dir / "apply_test_result.txt"
    script_path = out_dir / "apply_test.tcl"

    body_lines = [
        'if {![winfo exists .camera]} {error "missing widget .camera"}',
    ]

    # Write values
    for name, value in PARAMS.items():
        widget = WRITE_WIDGETS[name]
        body_lines.extend([
            f'if {{![winfo exists {widget}]}} {{error "missing widget {widget}"}}',
            f'{widget} delete 0 end',
            f'{widget} insert 0 {value}',
        ])

    # Apply
    body_lines.extend([
        'update idletasks',
        'if {![winfo exists .camera.btn.set]} {error "missing widget .camera.btn.set"}',
        '.camera.btn.set invoke',
        'update idletasks',
        'after 2000',
        'update idletasks',
    ])

    # Read back
    body_lines.append('set result {}')
    for name in PARAMS:
        read_widget = READ_WIDGETS[name]
        body_lines.extend([
            f'if {{![winfo exists {read_widget}]}} {{error "missing widget {read_widget}"}}',
            f'lappend result "{name}=[{read_widget} get]"',
        ])
    body_lines.append('join $result "\\n"')

    script_text = render_dde_execute_script(result_path, "IPG-MOVIE", body_lines)
    script_path.write_text(script_text, encoding="utf-8")

    print(f"Script written to: {script_path}")
    print(f"Result will be at: {result_path}")
    print()

    # Execute
    print("Executing via DDE RunScript...")
    try:
        run_runscript("TclEval", "CarMaker", script_path)
    except Exception as e:
        print(f"DDE error: {e}")

    # Read result
    time.sleep(1)
    if result_path.exists():
        result_text = result_path.read_text(encoding="utf-8", errors="replace")
        print(f"\nDDE result:\n{result_text}")
    else:
        print(f"\nNo result file at {result_path}")

    print("\n--- Check CarMaker 3D view: did the image change? ---")


if __name__ == "__main__":
    main()
