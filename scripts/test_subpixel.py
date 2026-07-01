"""Test: sub-pixel template matching on SIM vs real.

Hypothesis:
  Current template matching is integer-pixel → RMSE=1.000 for ALL params.
  If we use parabolic interpolation around the NCC peak to get sub-pixel
  precision, the RMSE may differentiate between param sets.

Approach:
  1. Use calibrator's SIM renders
  2. Extract template from real ROI
  3. Match with TM_CCOEFF_NORMED at integer level
  4. Fit 2D parabola to 3×3 neighborhood around peak
  5. Compute sub-pixel peak location
  6. Compare RMSE across 3 param sets
"""
import cv2, numpy as np, json, sys, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
OUT_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/subpixel_test")
OUT_DIR.mkdir(parents=True, exist_ok=True)

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

ROI = (673, 405, 497, 253)


def subpixel_peak(response_map):
    """Fit 2D parabola to 3x3 neighborhood of the max response.
    Returns (subpixel_x, subpixel_y, peak_value)."""
    _, _, _, max_loc = cv2.minMaxLoc(response_map)
    px, py = max_loc

    # Check bounds for 3x3 neighborhood
    h, w = response_map.shape
    if px < 1 or px >= w - 1 or py < 1 or py >= h - 1:
        return float(px), float(py), float(response_map[py, px])

    # 3x3 patch
    patch = response_map[py-1:py+2, px-1:px+2].astype(np.float64)

    # Parabolic fit for X: f(x) = ax^2 + bx + c
    # Using: x_offset = (f(x-1) - f(x+1)) / (2 * (2*f(x) - f(x-1) - f(x+1)))
    f_center = patch[1, 1]
    fx_left = patch[1, 0]
    fx_right = patch[1, 2]
    fy_top = patch[0, 1]
    fy_bottom = patch[2, 1]

    denom_x = 2 * f_center - fx_left - fx_right
    denom_y = 2 * f_center - fy_top - fy_bottom

    sub_x = float(px)
    sub_y = float(py)

    if abs(denom_x) > 1e-10:
        sub_x = float(px) + (fx_left - fx_right) / (2 * denom_x)
    if abs(denom_y) > 1e-10:
        sub_y = float(py) + (fy_top - fy_bottom) / (2 * denom_y)

    return sub_x, sub_y, float(f_center)


def anchors_from_subpixel(sub_x, sub_y, tw, th):
    """9 anchors from sub-pixel bounding box top-left."""
    return np.array([
        [sub_x, sub_y],
        [sub_x + tw - 1, sub_y],
        [sub_x + tw - 1, sub_y + th - 1],
        [sub_x, sub_y + th - 1],
        [sub_x + tw * 0.5, sub_y + th * 0.5],
        [sub_x + tw * 0.25, sub_y + th * 0.25],
        [sub_x + tw * 0.75, sub_y + th * 0.25],
        [sub_x + tw * 0.75, sub_y + th * 0.75],
        [sub_x + tw * 0.25, sub_y + th * 0.75],
    ], dtype=np.float32)


def rmse_between(a, b):
    n = min(len(a), len(b))
    deltas = a[:n] - b[:n]
    return float(np.sqrt(np.mean(np.square(np.linalg.norm(deltas, axis=1)))))


def run_one(label, params):
    safe = label.replace(" ", "_")
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    out_dir = OUT_DIR / safe
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load config, run calibrator
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    temp_cfg = _cfg_with_initial_values(cfg, params)
    temp_cfg["output_dir"] = str(out_dir)
    calib = CameraCalibrator(temp_cfg, config_path=CONFIG_PATH)
    calib._apply_initial_value_map_with_retry(params, label)
    calib.cfg["output_dir"] = str(out_dir)

    total_detail, sim_path = calib.evaluate("initial", baseline_metrics=None)

    # Get images
    real_img = calib.real_img
    raw_sim = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
    sim_eval = calib._prepare_eval_image(raw_sim)

    # Real anchors (reference)
    board_cfg = calib.boards[0]
    real_anchors = calib.real_detections[board_cfg.board_id].ordered_points

    # Calibrator's own result
    sim_anchors_orig = calib._last_sim_detections[board_cfg.board_id].ordered_points
    rmse_orig = rmse_between(sim_anchors_orig, real_anchors)

    # Get template info
    template_info = calib.custom_templates.get(board_cfg.board_id)
    template_gray = template_info["template"]
    tw, th = template_gray.shape[1], template_gray.shape[0]

    print(f"  Calibrator (integer): RMSE={rmse_orig:.3f}")
    print(f"  Template: {tw}x{th}")

    # --- Sub-pixel matching on SIM eval ---
    x, y, w, h = ROI
    pad = 40
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(sim_eval.shape[1], x + w + pad)
    y1 = min(sim_eval.shape[0], y + h + pad)
    search = sim_eval[y0:y1, x0:x1]

    result = cv2.matchTemplate(search, template_gray, cv2.TM_CCOEFF_NORMED)
    sub_x, sub_y, peak_val = subpixel_peak(result)
    sub_x_global = x0 + sub_x
    sub_y_global = y0 + sub_y

    # Integer anchors
    int_anchors = anchors_from_subpixel(
        x0 + float(int(sub_x)),
        y0 + float(int(sub_y)),
        tw, th
    )
    # Sub-pixel anchors
    sub_anchors = anchors_from_subpixel(sub_x_global, sub_y_global, tw, th)

    rmse_int = rmse_between(int_anchors, real_anchors)
    rmse_sub = rmse_between(sub_anchors, real_anchors)

    print(f"  My integer match:  RMSE={rmse_int:.3f}  (peak={peak_val:.4f})")
    print(f"  My sub-pixel match: RMSE={rmse_sub:.4f}  (offset=({sub_x_global-x0:.3f},{sub_y_global-y0:.3f}))")

    # --- Also test: upscale search & template by 2x, then match ---
    scale = 2
    search_big = cv2.resize(search, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    template_big = cv2.resize(template_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)

    result_big = cv2.matchTemplate(search_big, template_big, cv2.TM_CCOEFF_NORMED)
    sub_x2, sub_y2, peak_val2 = subpixel_peak(result_big)

    # Convert back to original coordinates
    sub_x_global2 = x0 + sub_x2 / scale
    sub_y_global2 = y0 + sub_y2 / scale
    sub_anchors2 = anchors_from_subpixel(sub_x_global2, sub_y_global2, tw, th)
    rmse_sub2 = rmse_between(sub_anchors2, real_anchors)

    print(f"  2x upscale + subpixel: RMSE={rmse_sub2:.4f}  (peak={peak_val2:.4f})")

    # --- Also test: 4x ---
    scale4 = 4
    search_big4 = cv2.resize(search, None, fx=scale4, fy=scale4, interpolation=cv2.INTER_LINEAR)
    template_big4 = cv2.resize(template_gray, None, fx=scale4, fy=scale4, interpolation=cv2.INTER_LINEAR)

    result_big4 = cv2.matchTemplate(search_big4, template_big4, cv2.TM_CCOEFF_NORMED)
    sub_x4, sub_y4, peak_val4 = subpixel_peak(result_big4)

    sub_x_global4 = x0 + sub_x4 / scale4
    sub_y_global4 = y0 + sub_y4 / scale4
    sub_anchors4 = anchors_from_subpixel(sub_x_global4, sub_y_global4, tw, th)
    rmse_sub4 = rmse_between(sub_anchors4, real_anchors)

    print(f"  4x upscale + subpixel: RMSE={rmse_sub4:.4f}  (peak={peak_val4:.4f})")

    # --- Up to 8x ---
    scale8 = 8
    search_big8 = cv2.resize(search, None, fx=scale8, fy=scale8, interpolation=cv2.INTER_LINEAR)
    template_big8 = cv2.resize(template_gray, None, fx=scale8, fy=scale8, interpolation=cv2.INTER_LINEAR)

    result_big8 = cv2.matchTemplate(search_big8, template_big8, cv2.TM_CCOEFF_NORMED)
    sub_x8, sub_y8, peak_val8 = subpixel_peak(result_big8)

    sub_x_global8 = x0 + sub_x8 / scale8
    sub_y_global8 = y0 + sub_y8 / scale8
    sub_anchors8 = anchors_from_subpixel(sub_x_global8, sub_y_global8, tw, th)
    rmse_sub8 = rmse_between(sub_anchors8, real_anchors)

    print(f"  8x upscale + subpixel: RMSE={rmse_sub8:.4f}  (peak={peak_val8:.4f})")

    return {
        "label": label,
        "rmse_orig": rmse_orig,
        "rmse_int": rmse_int,
        "rmse_sub": rmse_sub,
        "rmse_2x": rmse_sub2,
        "rmse_4x": rmse_sub4,
        "rmse_8x": rmse_sub8,
        "peak_val": peak_val,
    }


results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r:
        results.append(r)
    time.sleep(1)

# Summary
print(f"\n\n{'='*60}")
print(f"  SUMMARY — Sub-pixel Template Matching")
print(f"{'='*60}")
if len(results) >= 2:
    cols = ["Label", "Orig", "Int", "SubPx", "2x+Sub", "4x+Sub", "8x+Sub", "Peak"]
    hdr = "  " + "".join(f"{c:>10}" for c in cols)
    sep = "  " + "".join("-"*10 for _ in cols)
    print(f"\n{hdr}\n{sep}")
    for r in results:
        print(f"  {r['label']:>10} {r['rmse_orig']:>10.4f} {r['rmse_int']:>10.4f} {r['rmse_sub']:>10.4f} "
              f"{r['rmse_2x']:>10.4f} {r['rmse_4x']:>10.4f} {r['rmse_8x']:>10.4f} {r['peak_val']:>10.4f}")

    ref = results[0]
    print(f"\n  Differentiation (max Δ from {ref['label']}):")
    for metric in ["rmse_orig", "rmse_int", "rmse_sub", "rmse_2x", "rmse_4x", "rmse_8x"]:
        deltas = [abs(r[metric] - ref[metric]) for r in results[1:]]
        avg_delta = np.mean(deltas) if deltas else 0
        max_delta = max(deltas) if deltas else 0
        print(f"    {metric:<12}: avg Δ={avg_delta:.6f}, max Δ={max_delta:.6f}")
