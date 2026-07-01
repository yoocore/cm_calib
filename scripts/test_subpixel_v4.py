"""Test: sub-pixel refinement EXACTLY matching calibrator's search.

For custom_maker, padding attempts are [50, 99, 174].
First attempt at padding=50 succeeds at NCC threshold → match found.

Matching flow:
1. Extract ROI at offset (ROI.x - pad, ROI.y - pad), size (ROI.w+2*pad, ROI.h+2*pad)
2. Template match at raw gray (sim_sourced=True → no preprocessing)
3. Constrain valid positions where candidate center falls within expanded bbox
4. Return best valid match

Then add sub-pixel refinement.
"""
import cv2, numpy as np, json, sys, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
OUT_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/subpixel_v4_test")
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

ROI_X, ROI_Y, ROI_W, ROI_H = 673, 405, 497, 253
PAD = 50  # First padding attempt for custom_maker


def expand_bbox(bbox, img_shape):
    """Same as calibrator's _expand_bbox."""
    x, y, w, h = bbox
    ratio = 0.06
    min_pad = 12
    pad_w = max(min_pad, int(w * ratio))
    pad_h = max(min_pad, int(h * ratio))
    x0 = max(0, x - pad_w)
    y0 = max(0, y - pad_h)
    x1 = min(img_shape[1], x + w + pad_w)
    y1 = min(img_shape[0], y + h + pad_h)
    return (x0, y0, x1 - x0, y1 - y0)


def subpixel_peak_3x3(response_map):
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
    print(f"  {label}  (yaw={params['yaw']})")
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

    # Template info
    template_info = calib.custom_templates.get(board_cfg.board_id)
    template_gray = template_info["template"]
    content_bbox = template_info.get("content_bbox")
    if content_bbox is None:
        content_bbox = (0, 0, template_gray.shape[1], template_gray.shape[0])
    c_x, c_y, c_w, c_h = [float(v) for v in content_bbox]

    print(f"  Content bbox: ({c_x}, {c_y}, {c_w}, {c_h})")
    print(f"  Calibrator RMSE={rmse_orig:.3f}")

    # ============ Exact calibrator matching (padding=50) ============
    # Extract ROI with padding
    ox = max(0, ROI_X - PAD)
    oy = max(0, ROI_Y - PAD)
    ow = ROI_W + 2*PAD
    oh = ROI_H + 2*PAD
    if ox + ow > sim_eval.shape[1]:
        ow = sim_eval.shape[1] - ox
    if oy + oh > sim_eval.shape[0]:
        oh = sim_eval.shape[0] - oy

    roi_img = sim_eval[oy:oy+oh, ox:ox+ow]  # sim_sourced → no preprocessing
    result = cv2.matchTemplate(roi_img, template_gray, cv2.TM_CCOEFF_NORMED)

    # Best match (global, since all positions are within allowed bbox)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    dx_int, dy_int = max_loc
    match_x = ox + dx_int
    match_y = oy + dy_int

    # Apply content offset (no match_crop for first padding, content_x=content_y=0)
    anchor_x = match_x + c_x
    anchor_y = match_y + c_y
    int_anchors = anchors_from_bbox(anchor_x, anchor_y, c_w, c_h)
    rmse_int = rmse_between(int_anchors, real_anchors)

    print(f"  --- Calibrator-mimic (padding={PAD}) ---")
    print(f"  Integer:    anchor=({anchor_x:.0f},{anchor_y:.0f}) RMSE={rmse_int:.4f} NCC={max_val:.4f}")

    # Sub-pixel refinement
    sx, sy = subpixel_peak_3x3(result)
    sub_match_x = ox + sx
    sub_match_y = oy + sy
    sub_ax = sub_match_x + c_x
    sub_ay = sub_match_y + c_y
    sub_anchors = anchors_from_bbox(sub_ax, sub_ay, c_w, c_h)
    rmse_sub = rmse_between(sub_anchors, real_anchors)

    print(f"  Sub-pixel 1x: anchor=({sub_ax:.3f},{sub_ay:.3f}) RMSE={rmse_sub:.4f}")

    # Compare to calibrator
    first_calib = sim_anchors_orig[0]
    first_int = int_anchors[0]
    first_sub = sub_anchors[0]
    print(f"  Calib first anchor: ({first_calib[0]:.1f}, {first_calib[1]:.1f})")
    print(f"  Int   first anchor: ({first_int[0]:.1f}, {first_int[1]:.1f})")
    print(f"  Sub   first anchor: ({first_sub[0]:.3f}, {first_sub[1]:.3f})")

    # ============ Upscaled matching ============
    # Following your idea: zoom in on the ROI for more precise matching
    row_data = {"label": label, "rmse_orig": rmse_orig, "rmse_int": rmse_int,
                "rmse_sub": rmse_sub, "peak": max_val,
                "match_x": match_x, "match_y": match_y,
                "sub_x": sub_match_x, "sub_y": sub_match_y}

    for scale, sl in [(2, "2x"), (4, "4x"), (8, "8x")]:
        roi_big = cv2.resize(roi_img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        tmpl_big = cv2.resize(template_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        r = cv2.matchTemplate(roi_big, tmpl_big, cv2.TM_CCOEFF_NORMED)
        sx, sy = subpixel_peak_3x3(r)

        # Convert back to original coordinates
        gx = ox + sx / scale
        gy = oy + sy / scale
        g_ax = gx + c_x
        g_ay = gy + c_y
        ga = anchors_from_bbox(g_ax, g_ay, c_w, c_h)
        rms = rmse_between(ga, real_anchors)
        row_data[f"rmse_{sl}"] = rms
        print(f"  Sub-pixel {sl}: anchor=({g_ax:.4f},{g_ay:.4f}) RMSE={rms:.4f}")

    return row_data


all_results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r:
        all_results.append(r)
    time.sleep(1)

# Summary
print(f"\n\n{'='*60}")
print(f"  SUMMARY — Exact Calibrator Mimic + Sub-pixel")
print(f"{'='*60}")
if len(all_results) >= 2:
    cols = ["Label", "Orig", "Int", "Sub1x", "Sub2x", "Sub4x", "Sub8x", "Peak"]
    hdr = "  " + "".join(f"{c:>10}" for c in cols)
    sep = "  " + "".join("-"*10 for _ in cols)
    print(f"\n{hdr}\n{sep}")
    for r in all_results:
        v = r.get
        print(f"  {r['label']:>10} {v('rmse_orig',0):>10.4f} {v('rmse_int',0):>10.4f} {v('rmse_sub',0):>10.4f} "
              f"{v('rmse_2x',0):>10.4f} {v('rmse_4x',0):>10.4f} {v('rmse_8x',0):>10.4f} "
              f"{v('peak',0):>10.4f}")

    ref = all_results[0]
    print(f"\n  Differentiation (max Δ from {ref['label']}):")
    for metric in ["rmse_orig", "rmse_int", "rmse_sub", "rmse_2x", "rmse_4x", "rmse_8x"]:
        deltas = [abs(r.get(metric, 0) - ref.get(metric, 0)) for r in all_results[1:]
                 if r.get(metric, 0) > 0]
        if not deltas:
            continue
        print(f"    {metric:<12}: max Δ={max(deltas):.6f}")

    # Show sub-pixel match positions
    print(f"\n  Sub-pixel match positions (image coords):")
    for r in all_results:
        print(f"    {r['label']:<12}: calib=({r.get('match_x',-1):.0f},{r.get('match_y',-1):.0f}) "
              f"sub=({r.get('sub_x',-1):.4f},{r.get('sub_y',-1):.4f})")
