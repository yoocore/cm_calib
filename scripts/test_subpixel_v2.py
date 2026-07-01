"""Test: sub-pixel refinement in constrained search region.

The calibrator constrains template search to a small area around the
expected board position (from geometry). This consistently finds the
[-1,0] offset. Now we add sub-pixel refinement to see if RMSE
differentiates between param sets.

Approach:
  1. Run calibrator normally (gets integer matching at expected location)
  2. Extract the response map from constrained search
  3. Fit parabola to 3x3 neighborhood around the peak
  4. Compute sub-pixel anchors and RMSE
  5. Also test: upscale the constrained region by various factors
     and do higher-resolution match with sub-pixel refinement
"""
import cv2, numpy as np, json, sys, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
OUT_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/subpixel_v2_test")
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

EXPECTED_BBOX = [673, 405, 497, 253]  # template_source_roi = same as roi

# Per _template_match_best_local_candidate for custom_maker:
# expand_ratio = 0.06, expand_min_pad = 12
EXPAND_RATIO = 0.06
EXPAND_MIN_PAD = 12


def expand_bbox(bbox, img_shape, ratio=0.06, min_pad=12):
    x, y, w, h = bbox
    pad_w = max(min_pad, int(w * ratio))
    pad_h = max(min_pad, int(h * ratio))
    x0 = max(0, x - pad_w)
    y0 = max(0, y - pad_h)
    x1 = min(img_shape[1], x + w + pad_w)
    y1 = min(img_shape[0], y + h + pad_h)
    return (x0, y0, x1 - x0, y1 - y0)


def subpixel_peak(response_map):
    """Fit 2D parabola to 3x3 neighborhood around integer peak."""
    _, _, _, max_loc = cv2.minMaxLoc(response_map)
    px, py = max_loc
    h, w = response_map.shape
    if px < 1 or px >= w - 1 or py < 1 or py >= h - 1:
        return float(px), float(py), float(response_map[py, px])

    patch = response_map[py-1:py+2, px-1:px+2].astype(np.float64)
    f_center = patch[1, 1]
    fx_l, fx_r = patch[1, 0], patch[1, 2]
    fy_t, fy_b = patch[0, 1], patch[2, 1]

    sub_x = float(px)
    sub_y = float(py)
    dx_denom = 2 * f_center - fx_l - fx_r
    dy_denom = 2 * f_center - fy_t - fy_b
    if abs(dx_denom) > 1e-10:
        sub_x = float(px) + (fx_l - fx_r) / (2 * dx_denom)
    if abs(dy_denom) > 1e-10:
        sub_y = float(py) + (fy_t - fy_b) / (2 * dy_denom)
    return sub_x, sub_y, float(f_center)


def anchors_from_bbox(x, y, w, h):
    """9 anchors from a bounding box."""
    return np.array([
        [x, y],
        [x + w - 1, y],
        [x + w - 1, y + h - 1],
        [x, y + h - 1],
        [x + w * 0.5, y + h * 0.5],
        [x + w * 0.25, y + h * 0.25],
        [x + w * 0.75, y + h * 0.25],
        [x + w * 0.75, y + h * 0.75],
        [x + w * 0.25, y + h * 0.75],
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

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    temp_cfg = _cfg_with_initial_values(cfg, params)
    temp_cfg["output_dir"] = str(out_dir)
    calib = CameraCalibrator(temp_cfg, config_path=CONFIG_PATH)
    calib._apply_initial_value_map_with_retry(params, label)
    calib.cfg["output_dir"] = str(out_dir)

    total_detail, sim_path = calib.evaluate("initial", baseline_metrics=None)

    real_img = calib.real_img
    raw_sim = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
    sim_eval = calib._prepare_eval_image(raw_sim)

    board_cfg = calib.boards[0]
    real_anchors = calib.real_detections[board_cfg.board_id].ordered_points

    # Calibrator's own result
    sim_anchors_orig = calib._last_sim_detections[board_cfg.board_id].ordered_points
    rmse_orig = rmse_between(sim_anchors_orig, real_anchors)

    # Get template
    template_info = calib.custom_templates.get(board_cfg.board_id)
    template_gray = template_info["template"]
    tw, th = template_gray.shape[1], template_gray.shape[0]

    print(f"  Calibrator result: RMSE={rmse_orig:.3f}")

    # --- Constrained matching (as calibrator does it) ---
    # For custom_maker, sim_sourced = True → _preprocess = no-op (returns gray_image)
    allowed_bbox = expand_bbox(
        EXPECTED_BBOX, sim_eval.shape, ratio=EXPAND_RATIO, min_pad=EXPAND_MIN_PAD
    )
    ax, ay, aw, ah = allowed_bbox
    search = sim_eval[ay:ay+ah, ax:ax+aw]
    print(f"  Search region: {aw}x{ah} at offset ({ax},{ay})")

    # --- Method 1: calibrator-style integer match ---
    result = cv2.matchTemplate(search, template_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    int_x = ax + max_loc[0]
    int_y = ay + max_loc[1]
    int_anchors = anchors_from_bbox(int_x, int_y, tw, th)
    rmse_int = rmse_between(int_anchors, real_anchors)

    # --- Method 2: sub-pixel in original resolution ---
    sx, sy, _ = subpixel_peak(result)
    sub_x = ax + sx
    sub_y = ay + sy
    sub_anchors = anchors_from_bbox(sub_x, sub_y, tw, th)
    rmse_sub = rmse_between(sub_anchors, real_anchors)

    print(f"  Integer:        ({int_x:.1f},{int_y:.1f}) RMSE={rmse_int:.4f} peak={max_val:.4f}")
    print(f"  Sub-pixel 1x:   ({sub_x:.3f},{sub_y:.3f}) RMSE={rmse_sub:.4f}")

    # --- Method 3: 2x upscale then sub-pixel ---
    for scale, label_s in [(2, "2x"), (4, "4x"), (8, "8x")]:
        search_big = cv2.resize(search, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        template_big = cv2.resize(template_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        result_big = cv2.matchTemplate(search_big, template_big, cv2.TM_CCOEFF_NORMED)
        sx, sy, pv = subpixel_peak(result_big)
        gx = ax + sx / scale
        gy = ay + sy / scale
        sub_anchors_big = anchors_from_bbox(gx, gy, tw, th)
        rms = rmse_between(sub_anchors_big, real_anchors)
        setattr(locals(), f"rmse_{label_s.lower().replace('x','')}x", rms)
        print(f"  Sub-pixel {label_s}: ({gx:.4f},{gy:.4f}) RMSE={rms:.4f} peak={pv:.4f}")

    # --- Method 4: NCC vs RMSE sensitivity analysis ---
    # Small perturbations around the match position
    print(f"\n  Sensitivity (small perturbations around int match):")
    for dx in [-2, -1, -0.5, 0, 0.5, 1, 2]:
        dx_anchors = anchors_from_bbox(int_x + dx, int_y, tw, th)
        rms_dx = rmse_between(dx_anchors, real_anchors)
        print(f"    dx={dx:+3.1f}: RMSE={rms_dx:.4f}")

    return {
        "label": label, "rmse_orig": rmse_orig,
        "rmse_int": rmse_int, "rmse_sub": rmse_sub,
        "rmse_2x": locals().get("rmse_2x", 0),
        "rmse_4x": locals().get("rmse_4x", 0),
        "rmse_8x": locals().get("rmse_8x", 0),
        "peak_val": max_val,
    }


results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r:
        results.append(r)
    time.sleep(1)

# Summary
print(f"\n\n{'='*60}")
print(f"  SUMMARY — Sub-pixel in Constrained Search")
print(f"{'='*60}")
if len(results) >= 2:
    cols = ["Label", "Orig", "Int", "Sub1x", "Sub2x", "Sub4x", "Sub8x", "Peak"]
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
        avg_d = np.mean(deltas) if deltas else 0
        max_d = max(deltas) if deltas else 0
        print(f"    {metric:<12}: avg Δ={avg_d:.6f}, max Δ={max_d:.6f}")
