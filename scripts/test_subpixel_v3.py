"""Test: sub-pixel refinement matching calibrator's internal search.

The calibrator:
1. Constrains search to small region around expected position
2. Adjusts match position by content_bbox offset
3. Uses content_bbox width/height for anchors (not full template)

This test mimics that, then adds sub-pixel refinement.
"""
import cv2, numpy as np, json, sys, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
OUT_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/subpixel_v3_test")
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

EXPECTED_BBOX = [673, 405, 497, 253]
EXPAND_RATIO = 0.06
EXPAND_MIN_PAD = 12


def expand_bbox(bbox, img_shape):
    x, y, w, h = bbox
    pad_w = max(EXPAND_MIN_PAD, int(w * EXPAND_RATIO))
    pad_h = max(EXPAND_MIN_PAD, int(h * EXPAND_RATIO))
    x0 = max(0, x - pad_w)
    y0 = max(0, y - pad_h)
    x1 = min(img_shape[1], x + w + pad_w)
    y1 = min(img_shape[0], y + h + pad_h)
    return (x0, y0, x1 - x0, y1 - y0)


def subpixel_peak_3x3(response_map):
    """Parabolic fit to 3x3 around integer peak."""
    _, _, _, max_loc = cv2.minMaxLoc(response_map)
    px, py = max_loc
    h, w = response_map.shape
    if px < 1 or px >= w - 1 or py < 1 or py >= h - 1:
        return float(px), float(py)

    patch = response_map[py-1:py+2, px-1:px+2].astype(np.float64)
    fc = patch[1, 1]
    dx_n = 2*fc - patch[1,0] - patch[1,2]
    dy_n = 2*fc - patch[0,1] - patch[2,1]

    sx, sy = float(px), float(py)
    if abs(dx_n) > 1e-10:
        sx += (patch[1,0] - patch[1,2]) / (2*dx_n)
    if abs(dy_n) > 1e-10:
        sy += (patch[0,1] - patch[2,1]) / (2*dy_n)
    return sx, sy


def anchors_from_bbox(x, y, w, h):
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

    # Calibrator's result
    sim_anchors_orig = calib._last_sim_detections[board_cfg.board_id].ordered_points
    rmse_orig = rmse_between(sim_anchors_orig, real_anchors)

    # Get template and content_bbox
    template_info = calib.custom_templates.get(board_cfg.board_id)
    template_gray = template_info["template"]
    content_bbox = template_info.get("content_bbox")
    if content_bbox is None:
        content_bbox = (0, 0, template_gray.shape[1], template_gray.shape[0])
    c_x, c_y, c_w, c_h = [float(v) for v in content_bbox]
    tw_f, th_f = float(template_gray.shape[1]), float(template_gray.shape[0])

    print(f"  Template: {int(tw_f)}x{int(th_f)}")
    print(f"  Content bbox: ({c_x}, {c_y}, {c_w}, {c_h})")
    print(f"  Calibrator result: RMSE={rmse_orig:.3f}")

    # --- Constrained matching (mimicking calibrator) ---
    allowed = expand_bbox(EXPECTED_BBOX, sim_eval.shape)
    ax, ay, aw, ah = allowed
    search = sim_eval[ay:ay+ah, ax:ax+aw]
    result = cv2.matchTemplate(search, template_gray, cv2.TM_CCOEFF_NORMED)

    # Integer match
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    int_mx = ax + max_loc[0]  # match_x in full image coords
    int_my = ay + max_loc[1]  # match_y

    # Apply content offset (like calibrator does)
    anchor_x = int_mx - 0 + c_x  # crop_x = 0 for raw template
    anchor_y = int_my - 0 + c_y
    int_anchors = anchors_from_bbox(anchor_x, anchor_y, c_w, c_h)
    rmse_int = rmse_between(int_anchors, real_anchors)

    # Sub-pixel refinement
    sx, sy = subpixel_peak_3x3(result)
    sub_mx = ax + sx
    sub_my = ay + sy
    sub_ax = sub_mx + c_x
    sub_ay = sub_my + c_y
    sub_anchors = anchors_from_bbox(sub_ax, sub_ay, c_w, c_h)
    rmse_sub = rmse_between(sub_anchors, real_anchors)

    print(f"  Calibrator:   offset=({sim_anchors_orig[0,0]-real_anchors[0,0]:+.1f},{sim_anchors_orig[0,1]-real_anchors[0,1]:+.1f})")
    print(f"  My int:       anchor=({anchor_x:.1f},{anchor_y:.1f}) RMSE={rmse_int:.4f} peak={max_val:.4f}")
    print(f"  My sub-1x:    anchor=({sub_ax:.3f},{sub_ay:.3f}) RMSE={rmse_sub:.4f}")

    # Compare sub-pixel result to calibrator
    diff_from_calib = rmse_sub - rmse_orig
    print(f"  Δ from calibrator: {diff_from_calib:+.4f}")

    results_map = {"rmse_orig": rmse_orig, "rmse_int": rmse_int,
                   "rmse_sub": rmse_sub, "peak_val": max_val,
                   "int_mx": int_mx, "int_my": int_my,
                   "sub_mx": sub_mx, "sub_my": sub_my}

    # 2x, 4x, 8x upscale
    for scale, label_s in [(2, "2x"), (4, "4x"), (8, "8x")]:
        s_big = cv2.resize(search, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        t_big = cv2.resize(template_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        r_big = cv2.matchTemplate(s_big, t_big, cv2.TM_CCOEFF_NORMED)
        sx_b, sy_b = subpixel_peak_3x3(r_big)
        gx = ax + sx_b / scale
        gy = ay + sy_b / scale
        g_ax = gx + c_x
        g_ay = gy + c_y
        ga = anchors_from_bbox(g_ax, g_ay, c_w, c_h)
        rms = rmse_between(ga, real_anchors)
        results_map[f"rmse_{label_s}"] = rms
        print(f"  My sub-{label_s}:   anchor=({g_ax:.4f},{g_ay:.4f}) RMSE={rms:.4f}")

    return results_map


all_results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r:
        all_results.append(r)
    time.sleep(1)

# Summary
print(f"\n\n{'='*60}")
print(f"  SUMMARY — Sub-pixel in Constrained Search (with content offset)")
print(f"{'='*60}")
if len(all_results) >= 2:
    cols = ["Label", "Orig", "Int", "Sub1x", "Sub2x", "Sub4x", "Sub8x", "Peak"]
    hdr = "  " + "".join(f"{c:>10}" for c in cols)
    sep = "  " + "".join("-"*10 for _ in cols)
    print(f"\n{hdr}\n{sep}")
    for r in all_results:
        print(f"  {r['label']:>10} {r['rmse_orig']:>10.4f} {r['rmse_int']:>10.4f} {r['rmse_sub']:>10.4f} "
              f"{r.get('rmse_2x',0):>10.4f} {r.get('rmse_4x',0):>10.4f} {r.get('rmse_8x',0):>10.4f} "
              f"{r['peak_val']:>10.4f}")

    ref = all_results[0]
    print(f"\n  Differentiation (max Δ from {ref['label']}):")
    for metric in ["rmse_orig", "rmse_int", "rmse_sub", "rmse_2x", "rmse_4x", "rmse_8x"]:
        deltas = [abs(r.get(metric, 0) - ref.get(metric, 0)) for r in all_results[1:]]
        deltas = [d for d in deltas if d > 0]
        if not deltas:
            continue
        avg_d = np.mean(deltas)
        max_d = max(deltas)
        print(f"    {metric:<12}: avg Δ={avg_d:.6f}, max Δ={max_d:.6f}")

    # Also show raw match positions
    print(f"\n  Match positions (first anchor):")
    print(f"    {'Label':>12} {'Calib':>20} {'MyInt':>20} {'MySub':>20}")
    print(f"    {'-'*12} {'-'*20} {'-'*20} {'-'*20}")
    for r in all_results:
        print(f"    {r['label']:>12} ({r['rmse_orig']:.3f}) ({r['rmse_int']:.1f},{r['rmse_int']:.1f}) ({r['sub_mx']:.3f},{r['sub_my']:.3f})")
