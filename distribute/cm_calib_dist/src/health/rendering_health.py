"""IPG-MOVIE rendering health monitor and recovery.

Provides check-and-restart logic for the Movie rendering loop. Integrates with
dde_health_check.py for automated recovery.

Rendering Freeze Root Cause
---------------------------
UpdateView_TimerProc has a guard at line 10:
    if {($View(StopUpdateView) || $View(UpdateViewActive)) && $Pgm(Exporting)==0} {
        return
    }

When UpdateViewActive == 1 (set by a previous frame), the guard fires and the
function returns WITHOUT reaching "set View(UpdateViewActive) 0" at the end
(line 220). This leaves UVA permanently stuck at 1, causing every subsequent
call to bounce off the guard. The rendering loop is dead.

Fix: set UpdateViewActive=0 and call UpdateView_TimerProc directly. The single
call re-establishes the external rendering timer/loop, and UVA cycles correctly
between 0 (between frames) and 1 (during frame rendering).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from src.health.dde_health_check import (
    run_check_attempt,
    render_dde_execute_script,
    render_result_script,
)

_TMP = Path("tmp").resolve()


def _movie(tcl_body: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Execute Tcl in IPG-MOVIE via DDE TclEval."""
    name = f"mv_{uuid.uuid4().hex[:12]}"
    _TMP.mkdir(parents=True, exist_ok=True)
    script = render_dde_execute_script(_TMP / f"{name}.txt", "IPG-MOVIE", [tcl_body])
    return run_check_attempt(name, "TclEval", "CarMaker", _TMP, script, timeout)


def _carmaker(tcl_body: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Execute Tcl in CarMaker interpreter directly."""
    name = f"cm_{uuid.uuid4().hex[:12]}"
    _TMP.mkdir(parents=True, exist_ok=True)
    script = render_result_script(_TMP / f"{name}.txt", [tcl_body])
    return run_check_attempt(name, "TclEval", "CarMaker", _TMP, script, timeout)


def _parse_kv(detail: str, key: str) -> Any:
    """Parse a value from Tcl list output like 'UVA 1 UC 29445'."""
    parts = detail.split()
    try:
        idx = parts.index(key) + 1
        return parts[idx]
    except (ValueError, IndexError):
        return None


def check_render_state() -> Dict[str, Any]:
    """Check the current rendering state without modifying anything.

    Returns:
        Dict with keys: ok, uva, uc, suv, detail, elapsed_sec
    """
    started = time.perf_counter()
    r = _movie("set uva $::View(UpdateViewActive); set uc $::View(UpdateCounter); "
               "set suv $::View(StopUpdateView); set exp $::Pgm(Exporting); "
               "list UVA $uva UC $uc SUV $suv EXP $exp")
    elapsed = time.perf_counter() - started

    if not r.get("ok"):
        return {
            "ok": False,
            "error": f"State check failed: {r.get('kind')}: {r.get('detail', '')[:80]}",
            "elapsed_sec": elapsed,
        }

    detail = r.get("detail", "")
    return {
        "ok": True,
        "detail": detail,
        "uva": _parse_kv(detail, "UVA"),
        "uc": _parse_kv(detail, "UC"),
        "suv": _parse_kv(detail, "SUV"),
        "exp": _parse_kv(detail, "EXP"),
        "elapsed_sec": elapsed,
    }


def try_restart_rendering() -> Dict[str, Any]:
    """Check render state and restart if frozen.

    Returns:
        Dict with keys:
            rendering_was_frozen (bool)
            restart_success (bool)
            state_before (dict) — pre-restart render state
            state_after (dict) — post-restart render state (1s later)
            uc_growth (int) — frames rendered during verify window
            elapsed_sec (float)
            error (str, optional)
    """
    result = {
        "rendering_was_frozen": False,
        "restart_success": False,
        "state_before": {},
        "state_after": {},
        "uc_growth": 0,
        "elapsed_sec": None,
        "error": None,
    }
    started = time.perf_counter()

    # Step 1: Check current state
    state = check_render_state()
    if not state.get("ok"):
        result["error"] = state.get("error", "State check failed")
        result["elapsed_sec"] = time.perf_counter() - started
        return result

    result["state_before"] = state

    # Always attempt restart. The old check `if uva != 1: return success` was
    # removed because UVA=0 can also mean frozen rendering (TimerProc never
    # scheduled, rendering loop dead). We always try to kickstart it.
    result["rendering_was_frozen"] = state.get("uva") != "0" or state.get("suv") != "0"

    # Step 1b: Dismiss any Internal Debugger error dialog that blocks Movie's Tcl
    try:
        import ctypes
        _u = ctypes.windll.user32
        def _find_dbg() -> int:
            _f = []
            def _enum(h, _):
                _b = ctypes.create_unicode_buffer(256)
                _u.GetWindowTextW(h, _b, 256)
                if "Internal Debugger" in _b.value or ("IPGMovie" in _b.value and "Debugger" in _b.value):
                    _f.append(h)
                return True
            _c = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)(_enum)
            _u.EnumWindows(_c, 0)
            return _f[0] if _f else 0
        _hwnd = _find_dbg()
        if _hwnd:
            _u.PostMessageW(_hwnd, 0x0010, 0, 0)  # WM_CLOSE
            time.sleep(1.0)
    except Exception:
        pass

    # Step 2: Restart — set SUV=0, UVA=0, try to re-schedule rendering loop.
    # First try: direct call to UpdateView_TimerProc (fast path, works when Movie is healthy).
    # If that fails (RC!=0, likely NaN from uninitialized CSM), retry via `after` scheduling
    # which gives the event loop a chance to process pending initialization events
    # before the next render.
    r = _movie("""
        set ::View(StopUpdateView) 0
        set ::View(UpdateViewActive) 0
        set uc_before $::View(UpdateCounter)
        set rc [catch {UpdateView_TimerProc} msg]
        # Always re-schedule the rendering loop timer
        catch {after 0 UpdateView_TimerProc}
        # If direct call failed (RC!=0), also try scheduling via after with delay
        # This gives Tk event loop a chance to process CSM/GL initialization events
        if {$rc != 0} {
            set rc2 [catch {
                after 100 {set ::View(UpdateViewActive) 1; UpdateView_TimerProc}
            }]
            set msg2 $::errorInfo
        }
        set uc_after $::View(UpdateCounter)
        list RC $rc MSG $msg UC_BEFORE $uc_before UC_AFTER $uc_after
    """)

    if not r.get("ok"):
        result["error"] = f"Restart attempt failed: {r.get('kind')}: {r.get('detail', '')[:80]}"
        result["elapsed_sec"] = time.perf_counter() - started
        return result

    result["uc_before_restart"] = _parse_kv(r.get("detail", ""), "UC_BEFORE")
    result["rc"] = _parse_kv(r.get("detail", ""), "RC")

    # Step 3: Verify rendering is SUSTAINED over two intervals (not just a one-time bump).
    time.sleep(1.0)
    state_mid = check_render_state()
    uc_mid = int(state_mid.get("uc", 0) or 0) if state_mid.get("ok") else 0

    time.sleep(1.0)
    state2 = check_render_state()
    result["state_after"] = state2

    if state2.get("ok"):
        try:
            uc_after = int(state2["uc"])
            uc_before = int(result.get("uc_before_restart", 0) or 0)
            result["uc_growth"] = uc_after - uc_before
            # Require growth in BOTH intervals: mid-to-end confirms loop is sustained
            growth_second_half = uc_after - uc_mid
            result["restart_success"] = result["uc_growth"] > 0 and growth_second_half > 0
        except (TypeError, ValueError):
            pass

    result["elapsed_sec"] = time.perf_counter() - started
    return result


if __name__ == "__main__":
    import json

    state = check_render_state()
    print(f"Render state: ok={state.get('ok')} uva={state.get('uva')} "
          f"uc={state.get('uc')} suv={state.get('suv')} exp={state.get('exp')}")

    if state.get("uva") == "1":
        print("Rendering is FROZEN. Attempting restart...")
        r = try_restart_rendering()
        print(f"  was_frozen={r['rendering_was_frozen']} "
              f"success={r['restart_success']} "
              f"uc_growth={r['uc_growth']} "
              f"error={r.get('error')}")
        if r['restart_success']:
            print('>>> Rendering successfully restarted!')
        else:
            print(f'>>> Restart failed: {r.get("error")}')
    else:
        print('>>> Rendering is healthy.')
