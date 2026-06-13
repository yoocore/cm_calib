"""Diagnostic: Compare camera widget state BEFORE and AFTER script apply.

Usage:
  1. Make sure CarMaker is running with the camera you want to calibrate
  2. Run: python diagnose_camera_diff.py apply
     - This reads current state, applies rear_tv initial values, reads state again, shows diff
  3. Or: python diagnose_camera_diff.py read
     - This only reads and prints current state (for manual baseline)
"""
import sys
import json
import time
import tempfile
from pathlib import Path

try:
    import win32ui
    import dde
except ImportError:
    print("ERROR: pywin32 DDE support required.")
    sys.exit(1)

DDE_SERVICE = "TclEval"
DDE_TOPIC = "IPG-MOVIE"


def dde_exec_tcl(script_body: str, label: str = "diag") -> str:
    """Execute a Tcl script via DDE and return the result text."""
    result_path = Path(tempfile.gettempdir()) / f"cam_diag_{label}_{id(script_body):x}.txt"
    script_path = Path(tempfile.gettempdir()) / f"cam_diag_{label}_{id(script_body):x}.tcl"

    full_script = f"""set __diag_out [open "{result_path.as_posix()}" w]
set rc [catch {{send {DDE_TOPIC} {{
    {script_body}
}}} msg]
puts $__diag_out "rc=$rc"
puts $__diag_out "msg_begin"
puts $__diag_out $msg
puts $__diag_out "msg_end"
close $__diag_out
"""
    script_path.write_text(full_script, encoding="utf-8")

    server = None
    try:
        server = dde.CreateServer()
        server.Create(f"CamDiag.{label}.{id(script_body):x}")
        conv = dde.CreateConversation(server)
        conv.ConnectTo(DDE_SERVICE, DDE_TOPIC)
        conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
    except Exception as exc:
        print(f"DDE error ({label}): {exc}")
        return ""
    finally:
        if server:
            try:
                server.Shutdown()
            except Exception:
                pass

    deadline = time.time() + 10.0
    while time.time() < deadline:
        if result_path.exists():
            text = result_path.read_text(encoding="utf-8", errors="replace")
            if "msg_end" in text:
                break
        time.sleep(0.1)

    if not result_path.exists():
        return ""

    text = result_path.read_text(encoding="utf-8", errors="replace")

    # Extract message between msg_begin and msg_end
    lines = text.splitlines()
    in_msg = False
    msg_lines = []
    for line in lines:
        if line == "msg_begin":
            in_msg = True
            continue
        if line == "msg_end":
            in_msg = False
            continue
        if in_msg:
            msg_lines.append(line)

    try:
        result_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)
    except Exception:
        pass

    return "\n".join(msg_lines)


# Comprehensive list of all camera widgets
ALL_WIDGETS = {
    # Position / Rotation - entry values
    "pos_x (evptx)": ".camera.presetFrame.evptx",
    "pos_y (evpty)": ".camera.presetFrame.evpty",
    "pos_z (evptz)": ".camera.presetFrame.evptz",
    # Position / Rotation - set values
    "pos_x (svptx)": ".camera.presetFrame.svptx",
    "pos_y (svpty)": ".camera.presetFrame.svpty",
    "pos_z (svptz)": ".camera.presetFrame.svptz",
    # Rotation (same widget for entry and display)
    "roll (x)": ".camera.presetFrame.x",
    "pitch (y)": ".camera.presetFrame.y",
    "yaw (z)": ".camera.presetFrame.z",
    # FOV - entry
    "fov (efov)": ".camera.fovFrame.efov",
    # FOV - set/display
    "fov (sfov)": ".camera.fovFrame.sfov",
    # Camera model dialog - FOV
    "lens_fov (fov.e)": ".camera.cammoddlg.fov.e",
    # Camera model dialog - fisheye
    "lens_scale (fisheye.e1)": ".camera.cammoddlg.fisheye.ctrl.e1",
    "lens_offset_x (fisheye.e2)": ".camera.cammoddlg.fisheye.ctrl.e2",
    "lens_offset_y (fisheye.e3)": ".camera.cammoddlg.fisheye.ctrl.e3",
    # Camera model dialog - fisheye additional (may not exist)
    "fisheye.e4": ".camera.cammoddlg.fisheye.ctrl.e4",
    "fisheye.e5": ".camera.cammoddlg.fisheye.ctrl.e5",
    # Camera model dialog - distortion (may not exist)
    "distortion.e1": ".camera.cammoddlg.distortion.ctrl.e1",
    "distortion.e2": ".camera.cammoddlg.distortion.ctrl.e2",
    "distortion.e3": ".camera.cammoddlg.distortion.ctrl.e3",
    "distortion.e4": ".camera.cammoddlg.distortion.ctrl.e4",
    "distortion.e5": ".camera.cammoddlg.distortion.ctrl.e5",
    # Camera model dialog - type/model (may not exist)
    "type.m": ".camera.cammoddlg.type.m",
    "model.m": ".camera.cammoddlg.model.m",
    # Additional potential widgets
    "fovFrame.sfov2": ".camera.fovFrame.sfov2",
    "cammoddlg.fov.s": ".camera.cammoddlg.fov.s",
    "cammoddlg.fisheye.ctrl.s1": ".camera.cammoddlg.fisheye.ctrl.s1",
    "cammoddlg.fisheye.ctrl.s2": ".camera.cammoddlg.fisheye.ctrl.s2",
    "cammoddlg.fisheye.ctrl.s3": ".camera.cammoddlg.fisheye.ctrl.s3",
    # Camera type/frame widgets
    "camType.m": ".camera.camType.m",
    "camFrame.efov": ".camera.camFrame.efov",
}


def read_all_widgets() -> dict:
    """Read all camera widget values."""
    body_lines = ["set result {}"]
    for label, widget in ALL_WIDGETS.items():
        body_lines.append(f'if {{[winfo exists {widget}]}} {{')
        body_lines.append(f'    append result "{label}=[{widget} get]\\n"')
        body_lines.append(f'}} else {{')
        body_lines.append(f'    append result "{label}=NOT_FOUND\\n"')
        body_lines.append(f'}}')
    body_lines.append('set result')
    script_body = "    ".join(body_lines)
    msg = dde_exec_tcl(script_body, "read_all")
    
    result = {}
    for line in msg.splitlines():
        if "=" in line:
            name, val = line.split("=", 1)
            result[name.strip()] = val.strip()
    return result


def apply_params_via_script_control(params: dict):
    """Apply parameters by writing to widgets and clicking Set."""
    # Build Tcl script that writes values to entry widgets and invokes Set
    body_lines = [
        'if {![winfo exists .camera]} {error "missing widget .camera"}',
    ]
    
    # Map our param names to WRITE widgets
    write_map = {
        "pos_x": ".camera.presetFrame.evptx",
        "pos_y": ".camera.presetFrame.evpty",
        "pos_z": ".camera.presetFrame.evptz",
        "roll": ".camera.presetFrame.x",
        "pitch": ".camera.presetFrame.y",
        "yaw": ".camera.presetFrame.z",
        "lens_fov": ".camera.cammoddlg.fov.e",
        "lens_scale": ".camera.cammoddlg.fisheye.ctrl.e1",
        "lens_offset_x": ".camera.cammoddlg.fisheye.ctrl.e2",
        "lens_offset_y": ".camera.cammoddlg.fisheye.ctrl.e3",
    }
    
    decimals_map = {
        "pos_x": 4, "pos_y": 4, "pos_z": 4,
        "roll": 4, "pitch": 4, "yaw": 4,
        "lens_fov": 1, "lens_scale": 3,
        "lens_offset_x": 2, "lens_offset_y": 2,
    }
    
    for name, value in params.items():
        if name not in write_map:
            continue
        widget = write_map[name]
        dec = decimals_map.get(name, 4)
        body_lines.append(f'if {{![winfo exists {widget}]}} {{error "missing widget {widget}"}}')
        body_lines.append(f'{widget} delete 0 end')
        body_lines.append(f'{widget} insert 0 {value:.{dec}f}')
    
    body_lines.extend([
        'update idletasks',
        'if {![winfo exists .camera.btn.set]} {error "missing widget .camera.btn.set"}',
        '.camera.btn.set invoke',
        'update idletasks',
        'set result "applied"',
    ])
    
    script_body = "    ".join(body_lines)
    msg = dde_exec_tcl(script_body, "apply")
    return msg


def load_initial_values(config_path: str) -> dict:
    """Load initial values from config + Vehicle file reading."""
    # This mirrors what the calibration script does
    with open(config_path, "r") as f:
        cfg = json.load(f)
    
    # We need the initial values - read them from config if they exist
    params = cfg.get("parameters", {})
    initials = {}
    for name, p in params.items():
        if "initial" in p:
            initials[name] = p["initial"]
    
    if not initials:
        print("WARNING: No 'initial' values found in config. Using config defaults.")
        # Use a simple default
        for name, p in params.items():
            initials[name] = 0.0
    
    return initials


def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnose_camera_diff.py [read|apply|apply_and_compare]")
        print("  read              - Read and print current camera widget values")
        print("  apply             - Apply initial values and show before/after diff")
        print("  apply_and_compare - Apply, capture, and compare with reference image")
        sys.exit(1)
    
    mode = sys.argv[1]
    
    if mode == "read":
        print("=" * 60)
        print("Current Camera Widget Values")
        print("=" * 60)
        values = read_all_widgets()
        for label in sorted(values.keys()):
            val = values[label]
            marker = " ***" if val == "NOT_FOUND" else ""
            print(f"  {label:40s} = {val}{marker}")
        
    elif mode == "apply":
        config_path = sys.argv[2] if len(sys.argv) > 2 else "configs/camera.rear_tv.json"
        print("=" * 60)
        print(f"Before/After Diff for {config_path}")
        print("=" * 60)
        
        # Read BEFORE state
        print("\n--- Reading BEFORE state ---")
        before = read_all_widgets()
        
        # Load initial values
        initials = load_initial_values(config_path)
        print(f"\nInitial values to apply:")
        for name, val in sorted(initials.items()):
            print(f"  {name}: {val}")
        
        # Apply
        print(f"\n--- Applying parameters ---")
        result = apply_params_via_script_control(initials)
        print(f"Apply result: {result}")
        
        time.sleep(1.0)
        
        # Read AFTER state
        print(f"\n--- Reading AFTER state ---")
        after = read_all_widgets()
        
        # Show diff
        print(f"\n{'=' * 60}")
        print("DIFF (before vs after)")
        print(f"{'=' * 60}")
        
        all_labels = sorted(set(list(before.keys()) + list(after.keys())))
        diff_count = 0
        for label in all_labels:
            bv = before.get(label, "N/A")
            av = after.get(label, "N/A")
            if bv != av:
                diff_count += 1
                print(f"  {label:40s}: {bv:20s} -> {av:20s}")
        
        if diff_count == 0:
            print("  No differences found!")
        
        # Show all values
        print(f"\n{'=' * 60}")
        print("ALL AFTER values")
        print(f"{'=' * 60}")
        for label in sorted(after.keys()):
            val = after[label]
            marker = " ***" if val == "NOT_FOUND" else ""
            bv = before.get(label, "N/A")
            changed = " <-- CHANGED" if bv != val else ""
            print(f"  {label:40s} = {val:20s}{marker}{changed}")
    
    elif mode == "apply_and_compare":
        print("apply_and_compare mode - TODO")
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
