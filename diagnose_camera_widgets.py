"""Diagnostic: Read ALL camera widget values from CarMaker via DDE.

Usage:
  1. Load Vehicle file in CarMaker (this sets the known-good state)
  2. Run this script: python diagnose_camera_widgets.py
  3. Then apply params via calibration script
  4. Run again to see post-apply state
"""
import sys
import time

try:
    import win32ui
    import dde
except ImportError:
    print("ERROR: pywin32 DDE support required. Install pywin32.")
    sys.exit(1)

DDE_SERVICE = "TclEval"
DDE_TOPIC = "CarMaker"

# Widgets to read - comprehensive list covering camera model dialog
WIDGETS_TO_READ = {
    # Position / Rotation
    "presetFrame.evptx": ".camera.presetFrame.evptx",
    "presetFrame.evpty": ".camera.presetFrame.evpty",
    "presetFrame.evptz": ".camera.presetFrame.evptz",
    "presetFrame.svptx": ".camera.presetFrame.svptx",
    "presetFrame.svpty": ".camera.presetFrame.svpty",
    "presetFrame.svptz": ".camera.presetFrame.svptz",
    "presetFrame.x": ".camera.presetFrame.x",
    "presetFrame.y": ".camera.presetFrame.y",
    "presetFrame.z": ".camera.presetFrame.z",
    # FOV
    "fovFrame.efov": ".camera.fovFrame.efov",
    # Camera model dialog - FOV
    "cammoddlg.fov.e": ".camera.cammoddlg.fov.e",
    # Camera model dialog - fisheye
    "cammoddlg.fisheye.ctrl.e1": ".camera.cammoddlg.fisheye.ctrl.e1",
    "cammoddlg.fisheye.ctrl.e2": ".camera.cammoddlg.fisheye.ctrl.e2",
    "cammoddlg.fisheye.ctrl.e3": ".camera.cammoddlg.fisheye.ctrl.e3",
}

# Additional widgets to try reading (may or may not exist)
OPTIONAL_WIDGETS = {
    "cammoddlg.fisheye.ctrl.e4": ".camera.cammoddlg.fisheye.ctrl.e4",
    "cammoddlg.fisheye.ctrl.e5": ".camera.cammoddlg.fisheye.ctrl.e5",
    "cammoddlg.distortion.ctrl.e1": ".camera.cammoddlg.distortion.ctrl.e1",
    "cammoddlg.distortion.ctrl.e2": ".camera.cammoddlg.distortion.ctrl.e2",
    "cammoddlg.distortion.ctrl.e3": ".camera.cammoddlg.distortion.ctrl.e3",
    "cammoddlg.distortion.ctrl.e4": ".camera.cammoddlg.distortion.ctrl.e4",
    "cammoddlg.distortion.ctrl.e5": ".camera.cammoddlg.distortion.ctrl.e5",
    "cammoddlg.fov.s": ".camera.cammoddlg.fov.s",
    "cammoddlg.type.m": ".camera.cammoddlg.type.m",
    "cammoddlg.model.m": ".camera.cammoddlg.model.m",
    "fovFrame.sfov": ".camera.fovFrame.sfov",
}


def read_widgets(widget_map):
    """Read widget values via DDE."""
    body_lines = [
        "set result {}",
    ]
    for name, widget in widget_map.items():
        body_lines.append(f'if {{[winfo exists {widget}]}} {{')
        body_lines.append(f'    append result "{name}=[{widget} get]\\n"')
        body_lines.append(f'}} else {{')
        body_lines.append(f'    append result "{name}=WIDGET_NOT_FOUND\\n"')
        body_lines.append(f'}}')
    body_lines.append('set result')

    script_body = "\n    ".join(body_lines)
    script_text = f'send {DDE_TOPIC} {{\n    {script_body}\n}}'

    import tempfile
    from pathlib import Path
    result_path = Path(tempfile.gettempdir()) / "camera_diag_result.txt"
    script_path = Path(tempfile.gettempdir()) / "camera_diag_script.tcl"

    tcl_script = f"""set __diag_out [open "{result_path.as_posix()}" w]
set rc [catch {{send {DDE_TOPIC} {{
    {script_body}
}} msg]
puts $__diag_out "rc=$rc"
puts $__diag_out "msg_begin"
puts $__diag_out $msg
puts $__diag_out "msg_end"
close $__diag_out
"""
    script_path.write_text(tcl_script, encoding="utf-8")

    server = None
    try:
        server = dde.CreateServer()
        server.Create(f"CameraDiag.{id(script_path):x}")
        conv = dde.CreateConversation(server)
        conv.ConnectTo(DDE_SERVICE, DDE_TOPIC)
        conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
    except Exception as exc:
        print(f"DDE error: {exc}")
        return {}
    finally:
        if server:
            try:
                server.Shutdown()
            except Exception:
                pass

    # Wait for result
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if result_path.exists():
            text = result_path.read_text(encoding="utf-8", errors="replace")
            if "msg_end" in text:
                break
        time.sleep(0.1)

    if not result_path.exists():
        print("ERROR: No result file")
        return {}

    text = result_path.read_text(encoding="utf-8", errors="replace")

    # Parse
    values = {}
    in_msg = False
    for line in text.splitlines():
        if line.startswith("rc="):
            rc_val = line.split("=", 1)[1].strip()
            if rc_val != "0":
                print(f"WARNING: DDE rc={rc_val}")
        if line == "msg_begin":
            in_msg = True
            continue
        if line == "msg_end":
            in_msg = False
            continue
        if in_msg and "=" in line:
            name, val = line.split("=", 1)
            values[name.strip()] = val.strip()

    # Cleanup
    try:
        result_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)
    except Exception:
        pass

    return values


def main():
    print("=" * 60)
    print("CarMaker Camera Widget Diagnostic")
    print("=" * 60)
    print()

    all_widgets = {**WIDGETS_TO_READ, **OPTIONAL_WIDGETS}
    print(f"Reading {len(all_widgets)} widgets from CarMaker...")
    print()

    values = read_widgets(all_widgets)

    if not values:
        print("ERROR: Failed to read any widget values.")
        print("Make sure CarMaker is running with a vehicle loaded.")
        return

    print("--- Required Widgets ---")
    found_widgets = {}
    missing_widgets = {}
    for name, widget in WIDGETS_TO_READ.items():
        val = values.get(name, "MISSING")
        if val == "WIDGET_NOT_FOUND":
            missing_widgets[name] = widget
            print(f"  {name:40s} = WIDGET_NOT_FOUND")
        else:
            found_widgets[name] = val
            print(f"  {name:40s} = {val}")

    print()
    print("--- Optional Widgets (may or may not exist) ---")
    for name, widget in OPTIONAL_WIDGETS.items():
        val = values.get(name, "MISSING")
        if val == "WIDGET_NOT_FOUND":
            print(f"  {name:40s} = WIDGET_NOT_FOUND")
        else:
            print(f"  {name:40s} = {val}")

    print()
    print("--- Summary ---")
    print(f"Found: {len(found_widgets)}, Missing: {len(missing_widgets)}")

    if missing_widgets:
        print()
        print("Missing required widgets:")
        for name, widget in missing_widgets.items():
            print(f"  {name} -> {widget}")


if __name__ == "__main__":
    main()
