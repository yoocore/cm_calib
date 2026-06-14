"""Check CarMaker + IPG-MOVIE environment status.

Run before any calibration to verify environment is healthy.
Usage: python check_environment.py
"""
import sys
import os
import subprocess

sys.path.insert(0, ".")


def check_processes():
    """Check CarMaker and IPG-MOVIE processes."""
    print("=" * 50)
    print("1. Process Check")
    print("=" * 50)

    # Check CarMaker (note: process name is CarMaker.win64, not Carmaker)
    result = subprocess.run(
        ['powershell', '-Command',
         'Get-Process -Name "CarMaker*" -ErrorAction SilentlyContinue | '
         'Select-Object -First 3 Id, ProcessName, StartTime | Format-Table -AutoSize'],
        capture_output=True, text=True, timeout=10
    )
    if result.stdout.strip():
        print("CarMaker: FOUND")
        print(result.stdout)
    else:
        print("CarMaker: NOT FOUND (process may not be running)")
        return False

    # Check IPG-MOVIE
    result = subprocess.run(
        ['powershell', '-Command',
         'Get-Process -Name "Movie" -ErrorAction SilentlyContinue | '
         'Select-Object -First 3 Id, ProcessName, StartTime | Format-Table -AutoSize'],
        capture_output=True, text=True, timeout=10
    )
    if result.stdout.strip():
        print("IPG-MOVIE: FOUND")
        print(result.stdout)
    else:
        print("IPG-MOVIE: NOT FOUND")
        return False

    return True


def check_dde_state():
    """Check DDE connection and IPG-MOVIE state."""
    print("=" * 50)
    print("2. DDE + IPG-MOVIE State Check")
    print("=" * 50)

    from dde_health_check import run_check_attempt, default_output_dir, render_dde_execute_script
    from pathlib import Path

    output_dir = default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    result_file = str(output_dir / "env_check.txt").replace("\\", "/")

    body = [
        f'set __fp [open "{result_file}" w]',
        'puts $__fp "VIEW0:[winfo exists .view0]"',
        'puts $__fp "CHECKVP:[info commands CheckViewPort]"',
        'puts $__fp "CHECKVP_SAVED:[info commands CheckViewPort_saved]"',
        'puts $__fp "UPDATEVTIMER:[info commands UpdateView_TimerProc]"',
        'puts $__fp "TCL_VERSION:[info tclversion]"',
        'close $__fp',
    ]

    result = run_check_attempt(
        name="env_check",
        service="TclEval",
        topic="CarMaker",
        output_dir=output_dir,
        script_text=render_dde_execute_script(
            output_dir / "env_check.txt",
            "IPG-MOVIE",
            body,
        ),
        timeout_sec=10,
    )

    print(f"DDE connection: {'OK' if result.get('ok') else 'FAILED'}")

    if not result.get("ok"):
        print(f"  Detail: {result.get('detail')}")
        return False

    # Read and parse result
    if os.path.exists(result_file):
        with open(result_file) as f:
            content = f.read().strip()
            print(f"Result:\n{content}")

        # Parse results
        lines = content.split("\n")
        state = {}
        for line in lines:
            if ":" in line:
                key, _, value = line.partition(":")
                state[key.strip()] = value.strip()

        # Check critical states
        view0 = state.get("VIEW0", "unknown")
        checkvp = state.get("CHECKVP", "unknown")
        checkvp_saved = state.get("CHECKVP_SAVED", "unknown")

        print("\nState Analysis:")
        print(f"  .view0 exists: {view0}")

        if checkvp:
            print(f"  CheckViewPort: EXISTS (command: {checkvp})")
        else:
            print(f"  CheckViewPort: MISSING (command: '{checkvp}')")
            print("  ⚠️  WARNING: CheckViewPort not found - IPG-MOVIE may need restart!")

        if checkvp_saved:
            print(f"  CheckViewPort_saved: EXISTS (command: {checkvp_saved})")
        else:
            print(f"  CheckViewPort_saved: not present (normal)")

        # Overall assessment
        if view0 == "1" and checkvp:
            print("\n✅ Environment HEALTHY")
            return True
        elif view0 == "1" and not checkvp:
            print("\n❌ Environment UNHEALTHY: CheckViewPort missing")
            print("   Solution: Restart IPG-MOVIE")
            return False
        else:
            print("\n❌ Environment UNHEALTHY: .view0 not found")
            return False
    else:
        print("  Result file not created")
        return False


def main():
    print("CarMaker Environment Check")
    print("=" * 50)
    print()

    # Step 1: Process check
    if not check_processes():
        print("\n❌ Process check FAILED")
        sys.exit(1)

    print()

    # Step 2: DDE state check
    if not check_dde_state():
        print("\n❌ Environment check FAILED")
        sys.exit(1)

    print("\n✅ All checks passed")


if __name__ == "__main__":
    main()
