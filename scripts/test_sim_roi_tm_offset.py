"""Test: SIM-rendered-content as template → match on real image → measure offset.

Idea:
  - In the SIM image (rendered with current params), the board appears at some position
  - For each anchor, extract a small ROI around the SIM-detected anchor position
  - Use this SIM content as a template and match it against the real image
  - The offset between where the template best matches and the expected anchor position
    in the real image is the geometric error

This is purely geometric (measuring pixel displacement), not NCC-based scoring.
Template matching is only used to establish correspondence between SIM and real domains.
"""
import cv2
import numpy as np
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path(
    "C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0"
    "/camera.VehSensor_0.json"
)
TEMP_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/sim_roi_tm_test")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

PARAM_SETS = {
    "pool_best": {
        "yaw": 0.2502, "pos_x": 10.247, "pitch": 0.5088, "lens_fov": 120.0,
        "roll": -0.455, "pos_y": -0.127, "pos_z": 1.830,
        "lens_scale": 1.0, "lens_offset_x": 0.0, "lens_offset_y": 0.0,
    },
    "current_best": {
        "yaw": 0.2054, "pos_x": 10.243, "pitch": 0.4487, "lens_fov": 120.0,
        "roll": -0.475, "pos_y": -0.129, "pos_z": 1.828,
        "lens_scale": 1.0, "lens_offset_x": 0.0, "lens_offset_y": 0.0,
    },
    "custom": {
        "yaw": 0.1451, "pos_x": 10.233, "pitch": 0.3288, "lens_fov": 120.0,
        "roll": -0.475, "pos_y": -0.129, "pos_z": 1.828,
        "lens_scale": 1.0, "lens_offset_x": 0.0, "lens_offset_y": 0.0,
    },
}

# --- Config parameters for template matching ---
TM_RADIUS = 8          # template half-size: 17x17 px
SEARCH_MARGIN = 8      # extra search radius beyond template: total search = (TM_RADIUS+SEARCH_MARGIN)*2+1 = 33x33
TM_METHOD = cv2.TM_CCOEFF_NORMED
MIN_MATCH_VAL = 0.3    # minimum NCC to count a valid match


def sim_roi_tm_offset(
    sim_eval: np.ndarray,
    real_img: np.ndarray,
    sim_points: np.ndarray,
    real_points: np.ndarray,
    tm_radius: int = TM_RADIUS,
    search_margin: int = SEARCH_MARGIN,
    min_val: float = MIN_MATCH_VAL,
):
    """For each anchor: extract SIM ROI → TM on real image → measure displacement.

    Returns (avg_offset, valid_count, per_anchor_offsets, per_anchor_corrs).
    """
    total_offset = 0.0
    valid = 0
    offsets = []
    corrs = []

    for i in range(len(sim_points)):
        sx, sy = sim_points[i]
        rx, ry = real_points[i]
        sx_i, sy_i = int(round(sx)), int(round(sy))
        rx_i, ry_i = int(round(rx)), int(round(ry))

        # Extract SIM template patch
        h, w = sim_eval.shape[:2]
        if not (tm_radius <= sy_i < h - tm_radius and tm_radius <= sx_i < w - tm_radius):
            continue
        tmpl = sim_eval[sy_i - tm_radius : sy_i + tm_radius + 1,
                        sx_i - tm_radius : sx_i + tm_radius + 1]
        if tmpl.size == 0:
            continue

        # Search region in real image (centered on expected real anchor position)
        sr = tm_radius + search_margin
        rh, rw = real_img.shape[:2]
        if not (sr <= ry_i < rh - sr and sr <= rx_i < rw - sr):
            continue
        search = real_img[ry_i - sr : ry_i + sr + 1,
                          rx_i - sr : rx_i + sr + 1]

        # Template match
        result = cv2.matchTemplate(search, tmpl, TM_METHOD)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < min_val:
            continue

        # Convert match position back to real image coordinates
        # max_loc is top-left of best match in search region
        # The template center in search region = max_loc + tm_radius
        # The position in real image = rx_i - sr + max_loc + tm_radius
        match_x = rx_i - sr + max_loc[0] + tm_radius
        match_y = ry_i - sr + max_loc[1] + tm_radius

        offset = float(np.sqrt((match_x - rx_i) ** 2 + (match_y - ry_i) ** 2))
        total_offset += offset
        valid += 1
        offsets.append(offset)
        corrs.append(float(max_val))

    avg = total_offset / max(1, valid)
    return avg, valid, offsets, corrs


def run_one(label: str, params: dict):
    """Apply params, evaluate, get detections, compute SIM-ROI-TM offset."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    out_dir = TEMP_DIR / label
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    temp_cfg = _cfg_with_initial_values(cfg, params)
    temp_cfg["output_dir"] = str(out_dir)
    calib = CameraCalibrator(temp_cfg, config_path=CONFIG_PATH)
    calib._apply_initial_value_map_with_retry(params, label)

    calib.cfg["output_dir"] = str(out_dir)
    total_detail, sim_path = calib.evaluate("initial", baseline_metrics=None)

    # Get results from calibrator
    board_id = calib.boards[0].board_id
    real_det = calib.real_detections[board_id]
    sim_det = calib._last_sim_detections[board_id]
    real_img = calib.real_img

    # Read SIM image and prepare (resize to match real_img)
    raw_sim = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
    if raw_sim is None:
        print(f"  ERROR: cannot read sim image {sim_path}")
        return None

    sim_prepared = calib._prepare_eval_image(raw_sim)

    # Current scoring breakdown
    board_cfg = calib.boards[0]
    n = min(real_det.point_count, sim_det.point_count)
    if n > 0:
        deltas = sim_det.ordered_points[:n] - real_det.ordered_points[:n]
        dists = np.linalg.norm(deltas, axis=1)
        rmse = float(np.sqrt(np.mean(np.square(dists))))
        mean_e = float(np.mean(dists))
        max_e = float(np.max(dists))
    else:
        rmse = mean_e = max_e = 0.0

    # New: SIM-ROI-to-real TM offset
    avg_offset, valid, offsets, corrs = sim_roi_tm_offset(
        sim_prepared, real_img,
        sim_det.ordered_points[:n], real_det.ordered_points[:n],
    )

    print(f"  RMSE:            {rmse:.3f} px")
    print(f"  mean_error:      {mean_e:.3f} px")
    print(f"  max_error:       {max_e:.3f} px")
    print(f"  total_score:     {total_detail.total_score:.4f}")
    print(f"")
    print(f"  [SIM→Real TM]")
    print(f"  valid anchors:   {valid}/{n}")
    print(f"  avg offset:      {avg_offset:.3f} px")
    if offsets:
        print(f"  per-anchor:      {', '.join(f'{o:.2f}' for o in offsets)}")
        print(f"  correlations:    {', '.join(f'{c:.3f}' for c in corrs)}")
        print(f"  max offset:      {max(offsets):.3f} px")
        print(f"  offset > 2px:    {sum(1 for o in offsets if o > 2.0)}/{valid}")

    return {
        "label": label,
        "rmse": rmse,
        "mean_e": mean_e,
        "max_e": max_e,
        "score": total_detail.total_score,
        "tm_avg_offset": avg_offset,
        "tm_valid": valid,
        "tm_offsets": offsets,
        "tm_corrs": corrs,
        "params": params,
    }


# --- Run all param sets --------------------------------
results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r:
        results.append(r)
    time.sleep(1)

# --- Summary -------------------------------------------
print(f"\n\n{'='*60}")
print(f"  SUMMARY — SIM-ROI-to-Real Template Match Offset")
print(f"{'='*60}")
if len(results) >= 2:
    print(f"\n  {'Label':<20} {'RMSE':>8} {'TM_avg_offset':>15} {'Score':>10}")
    print(f"  {'-'*20} {'-'*8} {'-'*15} {'-'*10}")
    for r in results:
        print(f"  {r['label']:<20} {r['rmse']:>8.3f} {r['tm_avg_offset']:>15.3f} {r['score']:>10.4f}")

    # Check if TM offset differentiates
    ref = results[0]
    for r in results[1:]:
        delta = abs(r['tm_avg_offset'] - ref['tm_avg_offset'])
        print(f"\n  TM offset diff ({r['label']} - {ref['label']}): {delta:.3f} px")
        if delta > 0.5:
            print(f"  ✅ TM approach DIFFERENTIATES these parameter sets (Δ={delta:.3f}px)")
        else:
            print(f"  ❌ TM approach does NOT differentiate (Δ={delta:.3f}px, noise floor)")

print(f"\nDone.")
