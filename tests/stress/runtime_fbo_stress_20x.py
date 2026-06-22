"""Runtime 20x capture chain stress test for IPG-MOVIE FBO stability.

Three phases:
  1. Pure FBO capture (no prepare) x 20 — baseline
  2. ensure_movie_camera_selected("right_rear") -> FBO x 20
  3. Full prepare chain -> FBO x 20

Usage:
    python runtime_fbo_stress_20x.py [--phase 1|2|3|all] [--iterations 20]
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "Data" / "Script" / "CameraCalibration"))

from src.health.dde_health_check import render_dde_execute_script, run_check_attempt
from src.cmapi_testrun_control import (
    ensure_movie_abraxas_enabled,
    ensure_movie_camera_selected,
    ensure_movie_camera_widgets,
    ensure_movie_camera_dialogs_normal,
    ensure_movie_view_size,
)

DDE_SERVICE = "TclEval"
DDE_TOPIC = "CarMaker"
OUTPUT_BASE = Path("C:/CM_Projects/CMO141_Calibration/SimOutput/dde_health_check")
CONFIG_PATH = Path("C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/configs/camera.right_rear.json")


def _fbo_capture_probe(output_dir: Path, iteration: int) -> dict:
    """Minimal FBO new probe — returns {ok, elapsed_sec, detail}."""
    probe_dir = output_dir / f"iter_{iteration:03d}"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_name = f"fbo_probe_iter{iteration:03d}"
    result_path = probe_dir / f"{probe_name}.txt"

    body_lines = [
        "scan $View(ev.view) %d vno",
        'set wpath ".view$vno"',
        "set wi [$wpath.gl0 cget -width]",
        "set he [$wpath.gl0 cget -height]",
        "set captureFBO [FBO new $wi $he -tex rgb -noclear]",
        "set update_rc [catch {",
        "    FBO begin $captureFBO",
        "    UpdateView $vno",
        "    FBO end",
        "} update_msg]",
        "catch {FBO end}",
        "if {$update_rc != 0} {",
        "    catch {FBO delete $captureFBO}",
        "    error $update_msg",
        "}",
        "catch {FBO delete $captureFBO}",
        "format \"fbo=ok;size=${wi}x${he}\"",
    ]

    script_text = render_dde_execute_script(result_path, "IPG-MOVIE", body_lines)
    started = time.perf_counter()
    result = run_check_attempt(
        probe_name,
        DDE_SERVICE,
        DDE_TOPIC,
        probe_dir,
        script_text,
        timeout_sec=10.0,
    )
    elapsed = time.perf_counter() - started
    result["elapsed_sec"] = elapsed
    return result


def _load_view_size() -> tuple[int, int]:
    """Load width/height from camera config."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("movie_width", 960), cfg.get("movie_height", 640)


def run_phase1(output_dir: Path, iterations: int) -> list[dict]:
    """Phase 1: Pure FBO capture x N, no prepare helpers."""
    results = []
    print(f"\n=== Phase 1: Pure FBO capture x {iterations} ===")
    for i in range(iterations):
        r = _fbo_capture_probe(output_dir, i)
        status = "OK" if r.get("ok") else f"FAIL({r.get('kind')})"
        detail = r.get("detail", "")
        if detail and len(detail) > 80:
            detail = detail[:80] + "..."
        print(f"  iter {i+1:2d}/{iterations}: {status}  {r['elapsed_sec']:.2f}s  {detail}")
        results.append(r)
        time.sleep(0.3)
    return results


def run_phase2(output_dir: Path, iterations: int) -> list[dict]:
    """Phase 2: ensure_movie_camera_selected("right_rear") -> FBO x N."""
    results = []
    print(f"\n=== Phase 2: camera_selected(\"right_rear\") -> FBO x {iterations} ===")
    for i in range(iterations):
        phase_dir = output_dir / f"iter_{i:03d}"
        phase_dir.mkdir(parents=True, exist_ok=True)
        iter_result = {"iteration": i, "steps": []}

        # Step A: ensure_camera_selected
        t0 = time.perf_counter()
        try:
            sel = ensure_movie_camera_selected("right_rear", timeout_sec=8.0)
            sel_ok = True
            sel_detail = sel.get("mode", "")
        except Exception as exc:
            sel_ok = False
            sel_detail = str(exc)
        sel_elapsed = time.perf_counter() - t0
        iter_result["steps"].append({
            "step": "camera_selected",
            "ok": sel_ok,
            "elapsed_sec": round(sel_elapsed, 3),
            "detail": sel_detail[:120] if sel_detail else "",
        })
        status_a = "OK" if sel_ok else "FAIL"
        print(f"  iter {i+1:2d}/{iterations} A: camera_selected {status_a}  {sel_elapsed:.2f}s")

        # Step B: FBO probe
        t0 = time.perf_counter()
        fbo = _fbo_capture_probe(phase_dir, i)
        fbo_elapsed = time.perf_counter() - t0
        fbo_ok = fbo.get("ok", False)
        iter_result["steps"].append({
            "step": "fbo_probe",
            "ok": fbo_ok,
            "elapsed_sec": round(fbo_elapsed, 3),
            "kind": fbo.get("kind", ""),
            "detail": (fbo.get("detail") or "")[:120],
        })
        status_b = "OK" if fbo_ok else f"FAIL({fbo.get('kind')})"
        print(f"  iter {i+1:2d}/{iterations} B: fbo_probe       {status_b}  {fbo_elapsed:.2f}s")

        iter_result["ok"] = sel_ok and fbo_ok
        results.append(iter_result)
        time.sleep(0.3)
    return results


def run_phase3(output_dir: Path, iterations: int) -> list[dict]:
    """Phase 3: Full prepare chain -> FBO x N."""
    width, height = _load_view_size()
    results = []
    print(f"\n=== Phase 3: Full prepare chain -> FBO x {iterations} ===")
    print(f"  view size: {width}x{height}")
    for i in range(iterations):
        phase_dir = output_dir / f"iter_{i:03d}"
        phase_dir.mkdir(parents=True, exist_ok=True)
        iter_result = {"iteration": i, "steps": []}
        all_ok = True

        helpers = [
            ("abraxas", lambda: ensure_movie_abraxas_enabled(timeout_sec=8.0)),
            ("view_size", lambda: ensure_movie_view_size(width, height, timeout_sec=8.0)),
            ("camera_selected", lambda: ensure_movie_camera_selected("right_rear", timeout_sec=8.0)),
            ("camera_widgets", lambda: ensure_movie_camera_widgets(timeout_sec=8.0)),
            ("camera_dialogs", lambda: ensure_movie_camera_dialogs_normal(timeout_sec=8.0)),
        ]

        for name, fn in helpers:
            t0 = time.perf_counter()
            try:
                res = fn()
                ok = True
                detail = res.get("mode", "")
            except Exception as exc:
                ok = False
                detail = str(exc)
                all_ok = False
            elapsed = time.perf_counter() - t0
            iter_result["steps"].append({
                "step": name,
                "ok": ok,
                "elapsed_sec": round(elapsed, 3),
                "detail": (detail or "")[:120],
            })
            tag = "OK" if ok else "FAIL"
            print(f"  iter {i+1:2d}/{iterations} {name:18s} {tag}  {elapsed:.2f}s")
            if not ok:
                break

        # FBO probe after full prepare
        if all_ok:
            t0 = time.perf_counter()
            fbo = _fbo_capture_probe(phase_dir, i)
            fbo_elapsed = time.perf_counter() - t0
            fbo_ok = fbo.get("ok", False)
            iter_result["steps"].append({
                "step": "fbo_probe",
                "ok": fbo_ok,
                "elapsed_sec": round(fbo_elapsed, 3),
                "kind": fbo.get("kind", ""),
                "detail": (fbo.get("detail") or "")[:120],
            })
            all_ok = all_ok and fbo_ok
            tag = "OK" if fbo_ok else f"FAIL({fbo.get('kind')})"
            print(f"  iter {i+1:2d}/{iterations} {'fbo_probe':18s} {tag}  {fbo_elapsed:.2f}s")

        iter_result["ok"] = all_ok
        results.append(iter_result)
        time.sleep(0.5)
    return results


def _summarize(phase_name: str, results: list[dict]) -> dict:
    """Compute summary for a phase."""
    total = len(results)
    if not results:
        return {"phase": phase_name, "total": 0}

    # For phase1, results are flat dicts from run_check_attempt
    if "steps" not in results[0]:
        ok_count = sum(1 for r in results if r.get("ok"))
        fbo_fail = [r for r in results if not r.get("ok")]
        fbo_errors = []
        for r in fbo_fail:
            detail = r.get("detail", "")
            if "FBO" in detail or "FrameBuffer" in detail:
                fbo_errors.append(detail[:100])
        return {
            "phase": phase_name,
            "total": total,
            "fbo_ok": ok_count,
            "fbo_fail": total - ok_count,
            "fbo_error_details": fbo_errors,
        }

    # For phase2/3, results have nested steps
    ok_count = sum(1 for r in results if r.get("ok"))
    step_failures = {}
    for r in results:
        for s in r.get("steps", []):
            if not s.get("ok"):
                step_failures.setdefault(s["step"], 0)
                step_failures[s["step"]] += 1
    return {
        "phase": phase_name,
        "total": total,
        "all_ok": ok_count,
        "any_fail": total - ok_count,
        "step_failures": step_failures,
    }


def main():
    parser = argparse.ArgumentParser(description="FBO stress test 20x")
    parser.add_argument("--phase", type=int, default=0, choices=[0, 1, 2, 3],
                        help="Phase to run (0=all, 1=pure FBO, 2=camera_selected+FBO, 3=full chain+FBO)")
    parser.add_argument("--iterations", type=int, default=20, help="Iterations per phase")
    args = parser.parse_args()

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_BASE / f"{timestamp}_fbo_stress_20x"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output: {run_dir}")
    print(f"Timestamp: {timestamp}")
    print(f"Iterations: {args.iterations}")

    summaries = []

    if args.phase in (0, 1):
        p1_dir = run_dir / "phase1_pure_fbo"
        p1_results = run_phase1(p1_dir, args.iterations)
        s1 = _summarize("phase1_pure_fbo", p1_results)
        summaries.append(s1)
        (p1_dir / "results.json").write_text(json.dumps(p1_results, indent=2, default=str), encoding="utf-8")

    if args.phase in (0, 2):
        p2_dir = run_dir / "phase2_camera_selected_fbo"
        p2_results = run_phase2(p2_dir, args.iterations)
        s2 = _summarize("phase2_camera_selected_fbo", p2_results)
        summaries.append(s2)
        (p2_dir / "results.json").write_text(json.dumps(p2_results, indent=2, default=str), encoding="utf-8")

    if args.phase in (0, 3):
        p3_dir = run_dir / "phase3_full_chain_fbo"
        p3_results = run_phase3(p3_dir, args.iterations)
        s3 = _summarize("phase3_full_chain_fbo", p3_results)
        summaries.append(s3)
        (p3_dir / "results.json").write_text(json.dumps(p3_results, indent=2, default=str), encoding="utf-8")

    # Final summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for s in summaries:
        print(f"\n{s['phase']}: {json.dumps(s, indent=2)}")

    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2, default=str), encoding="utf-8")
    print(f"\nSummary saved to: {summary_path}")


if __name__ == "__main__":
    main()
