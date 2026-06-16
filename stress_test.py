#!/usr/bin/env python3
"""Stress test: 30 smoke + 5 full calibration runs.
Records results to stress_test_summary.json after each round.
"""
import json, subprocess, sys, time, os
from pathlib import Path

WORKDIR = Path(__file__).parent.resolve()
SUMMARY = WORKDIR / "stress_test_summary.json"
ORCHESTRATOR = WORKDIR / "calibration_orchestrator.py"
SIMOUT = Path(r"C:\CM_Projects\CMO141_Calibration\SimOutput\camera_orchestration")
LOG = WORKDIR / "tmp" / "stress_run.log"

HIL = r"D:\IPG\carmaker\win64-14.1\GUI\HIL.exe"
CARMAKER_ARGS = ["-projectdir", r"C:\CM_Projects\CMO141_Calibration"]
CARMAKER_CWD = r"D:\IPG\carmaker\win64-14.1\GUI"

def kill_all():
    for name in ("HIL.exe", "Movie.exe", "CarMaker.win64.exe"):
        subprocess.run(f"taskkill /IM {name} /F /T", shell=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)

def run_round(extra_args=None) -> dict:
    cmd = [sys.executable, str(ORCHESTRATOR), "--testrun", "vctc_ngxpro",
           "--camera", "left_tv", "--camera", "rear_tv", "--camera", "right_rear"]
    if extra_args:
        cmd.extend(extra_args)
    t0 = time.monotonic()
    with open(LOG, "w") as f:
        result = subprocess.run(cmd, cwd=WORKDIR, stdout=f, stderr=subprocess.STDOUT, timeout=600)
    elapsed = round(time.monotonic() - t0)

    dirs = sorted(SIMOUT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if SIMOUT.is_dir() else []
    if dirs:
        tsk = dirs[0] / "task_summary.json"
        if tsk.exists():
            j = json.loads(tsk.read_text())
            scores = {c["camera"]: c.get("calibration", {}).get("final_score")
                      for c in j.get("per_camera", [])}
            return {"status": j.get("status", "unknown"), "elapsed": elapsed,
                    "scores": scores, "dir": dirs[0].name}
    return {"status": "CRASHED", "elapsed": elapsed, "scores": {}}

def load_results():
    if SUMMARY.exists():
        return json.loads(SUMMARY.read_text())
    return {"smoke": [], "full": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

def save_results(results):
    SUMMARY.write_text(json.dumps(results, indent=2, ensure_ascii=False))

def main():
    results = load_results()

    kills = 0
    fail_total = 0
    results.setdefault("started", time.strftime("%Y-%m-%dT%H:%M:%S"))

    # --- 30 Smoke Tests ---
    print("=== SMOKE TEST (30 rounds) ===")
    for i in range(1, 31):
        print(f"\n--- Smoke Round {i}/30 {time.strftime('%H:%M:%S')} ---", flush=True)
        kill_all()
        kills += 1
        r = run_round()
        if r["status"] != "finished":
            fail_total += 1
        r["round"] = i
        r["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        results["smoke"].append(r)
        save_results(results)
        scores_str = " ".join(f"{k}={v}" for k, v in r["scores"].items())
        print(f"  {r['status']} {r['elapsed']}s  {scores_str}", flush=True)

    # --- 5 Full Calibration ---
    print(f"\n=== FULL CALIBRATION (5 rounds, explore-then-refine + 100 iters) ===")
    for i in range(1, 6):
        print(f"\n--- Full Round {i}/5 {time.strftime('%H:%M:%S')} ---", flush=True)
        kill_all()
        kills += 1
        r = run_round(["--explore-then-refine", "--multi-start-iters", "100", "--refine-iters", "100"])
        if r["status"] != "finished":
            fail_total += 1
        r["round"] = i
        r["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        results["full"].append(r)
        save_results(results)
        scores_str = " ".join(f"{k}={v}" for k, v in r["scores"].items())
        print(f"  {r['status']} {r['elapsed']}s  {scores_str}", flush=True)

    results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_results(results)
    print(f"\n=== ALL TESTS COMPLETE ===")
    print(f"  Smoke: {len(results['smoke'])}/{len([r for r in results['smoke'] if r['status']=='finished'])} passed")
    print(f"  Full:  {len(results['full'])}/{len([r for r in results['full'] if r['status']=='finished'])} passed")
    print(f"  Total failures: {fail_total}")

if __name__ == "__main__":
    main()
