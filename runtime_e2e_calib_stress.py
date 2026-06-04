"""End-to-end production calibration chain stress test.

Uses the real CameraCalibrator.capture_movie() and evaluate() paths,
not just FBO probes. Verifies PNG output validity and scoring.

Usage:
    python runtime_e2e_calib_stress.py [--iterations 20] [--include-evaluate]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "Data" / "Script" / "CameraCalibration"))

from camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/configs/camera.right_rear.json")
OUTPUT_BASE = Path("C:/CM_Projects/CMO141_Calibration/SimOutput/dde_health_check")


def _validate_png(path: Path, expected_size: tuple[int, int] | None = None) -> dict:
    """Validate a captured PNG file."""
    info: dict = {"path": str(path)}
    if not path.exists():
        info["valid"] = False
        info["reason"] = "file_missing"
        return info
    size_bytes = path.stat().st_size
    info["size_bytes"] = size_bytes
    if size_bytes < 1000:
        info["valid"] = False
        info["reason"] = f"file_too_small({size_bytes})"
        return info
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        info["valid"] = False
        info["reason"] = "cv2_read_failed"
        return info
    h, w = img.shape[:2]
    info["dimensions"] = f"{w}x{h}"
    info["mean"] = round(float(img.mean()), 2)
    info["std"] = round(float(img.std()), 2)
    if expected_size and (w, h) != expected_size:
        info["valid"] = False
        info["reason"] = f"dim_mismatch(expected={expected_size}, got={w}x{h})"
        return info
    if img.mean() < 1.0:
        info["valid"] = False
        info["reason"] = "image_blank"
        return info
    info["valid"] = True
    return info


def run_capture_stress(calib: CameraCalibrator, iterations: int, output_dir: Path) -> list[dict]:
    """Stress test capture_movie() x N with PNG validation."""
    results = []
    print(f"\n=== capture_movie() stress test x {iterations} ===")
    for i in range(iterations):
        tag = f"e2e_stress_{i:03d}"
        t0 = time.perf_counter()
        try:
            img_path = calib.capture_movie(tag)
            elapsed = time.perf_counter() - t0
            validation = _validate_png(img_path, expected_size=(960, 640))
            ok = validation.get("valid", False)
            detail = f"dim={validation.get('dimensions','?')} mean={validation.get('mean','?')} std={validation.get('std','?')}"
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            ok = False
            img_path = None
            validation = {"valid": False, "reason": str(exc)[:120]}
            detail = str(exc)[:80]
        status = "OK" if ok else f"FAIL"
        print(f"  iter {i+1:2d}/{iterations}: {status}  {elapsed:.2f}s  {detail}")
        results.append({
            "iteration": i,
            "ok": ok,
            "elapsed_sec": round(elapsed, 3),
            "validation": validation,
            "path": str(img_path) if img_path else None,
        })
        time.sleep(0.3)
    return results


def run_evaluate_stress(calib: CameraCalibrator, iterations: int, output_dir: Path) -> list[dict]:
    """Stress test evaluate() x N — full capture + scoring."""
    results = []
    print(f"\n=== evaluate() stress test x {iterations} ===")
    for i in range(iterations):
        tag = f"e2e_eval_{i:03d}"
        t0 = time.perf_counter()
        try:
            total_detail, sim_path = calib.evaluate(tag, baseline_metrics=None)
            elapsed = time.perf_counter() - t0
            score = total_detail.total_score
            n_boards = len(total_detail.board_scores) if total_detail.board_scores else 0
            validation = _validate_png(sim_path)
            ok = validation.get("valid", False) and score > 0
            detail = f"score={score:.2f} boards={n_boards} dim={validation.get('dimensions','?')} mean={validation.get('mean','?')}"
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            ok = False
            score = -1
            n_boards = 0
            detail = str(exc)[:100]
            validation = {"valid": False, "reason": str(exc)[:120]}
        status = "OK" if ok else "FAIL"
        print(f"  iter {i+1:2d}/{iterations}: {status}  {elapsed:.2f}s  {detail}")
        results.append({
            "iteration": i,
            "ok": ok,
            "elapsed_sec": round(elapsed, 3),
            "score": round(score, 4) if score > 0 else None,
            "n_boards": n_boards,
            "validation": validation,
        })
        time.sleep(0.5)
    return results


def main():
    parser = argparse.ArgumentParser(description="E2E calibration chain stress test")
    parser.add_argument("--iterations", type=int, default=20, help="Iterations per test")
    parser.add_argument("--include-evaluate", action="store_true", help="Also run evaluate() stress test")
    parser.add_argument("--capture-only", type=int, default=0,
                        help="Run only capture_movie() x N (overrides --iterations)")
    args = parser.parse_args()

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_BASE / f"{timestamp}_e2e_calib_stress"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Config: {CONFIG_PATH}")
    print(f"Output: {run_dir}")
    print(f"Iterations: {args.iterations}")

    # Load config and create calibrator
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    print("\nInitializing CameraCalibrator...")
    calib = CameraCalibrator(cfg, config_path=CONFIG_PATH)
    print(f"  output_dir: {calib.output_dir}")
    print(f"  real_image: {calib.real_image_path if hasattr(calib, 'real_image_path') else 'N/A'}")

    summaries = []

    # Phase 1: capture_movie() stress
    n_capture = args.capture_only if args.capture_only > 0 else args.iterations
    p1_dir = run_dir / "capture_stress"
    p1_dir.mkdir(parents=True, exist_ok=True)
    p1_results = run_capture_stress(calib, n_capture, p1_dir)
    ok_count = sum(1 for r in p1_results if r.get("ok"))
    s1 = {
        "phase": "capture_movie_stress",
        "total": len(p1_results),
        "ok": ok_count,
        "fail": len(p1_results) - ok_count,
        "invalid_images": [r for r in p1_results if not r.get("ok")],
    }
    summaries.append(s1)
    (p1_dir / "results.json").write_text(json.dumps(p1_results, indent=2, default=str), encoding="utf-8")

    # Phase 2: evaluate() stress (optional)
    if args.include_evaluate:
        p2_dir = run_dir / "evaluate_stress"
        p2_dir.mkdir(parents=True, exist_ok=True)
        p2_results = run_evaluate_stress(calib, args.iterations, p2_dir)
        ok_count = sum(1 for r in p2_results if r.get("ok"))
        s2 = {
            "phase": "evaluate_stress",
            "total": len(p2_results),
            "ok": ok_count,
            "fail": len(p2_results) - ok_count,
            "invalid_evaluations": [r for r in p2_results if not r.get("ok")],
        }
        summaries.append(s2)
        (p2_dir / "results.json").write_text(json.dumps(p2_results, indent=2, default=str), encoding="utf-8")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for s in summaries:
        print(f"\n{s['phase']}: {s['total']} total, {s['ok']} OK, {s['fail']} FAIL")

    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2, default=str), encoding="utf-8")
    print(f"\nSummary saved to: {summary_path}")


if __name__ == "__main__":
    main()
