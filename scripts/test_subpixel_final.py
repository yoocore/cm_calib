"""Test: local sub-pixel refinement around calibrator's match position.

The calibrator finds integer match_x, match_y. All three param sets
give the SAME integer position. Does sub-pixel refinement reveal
sub-pixel differences?

Strategy:
1. Run calibrator → get match_x, match_y + template
2. Do small local template match (pad+20 around match) to get response
3. Parabolic fit on 3×3 peak → sub-pixel coordinates
4. Compare across param sets
"""
import cv2, numpy as np, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
OUT_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/subpixel_final_test")
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


def subpixel_peak(response):
    """Parabolic fit around the max of a response map."""
    _, _, _, m = cv2.minMaxLoc(response)
    px, py = m
    h, w = response.shape
    if px < 1 or px >= w-1 or py < 1 or py >= h-1:
        return float(px), float(py)
    p = response[py-1:py+2, px-1:px+2].astype(np.float64)
    fc = p[1,1]
    sx, sy = float(px), float(py)
    dx = 2*fc - p[1,0] - p[1,2]
    dy = 2*fc - p[0,1] - p[2,1]
    if abs(dx) > 1e-10:
        sx += (p[1,0] - p[1,2]) / (2*dx)
    if abs(dy) > 1e-10:
        sy += (p[0,1] - p[2,1]) / (2*dy)
    return sx, sy


def anchors_9(x, y, w, h):
    return np.array([
        [x, y], [x+w-1, y], [x+w-1, y+h-1], [x, y+h-1],
        [x+w*0.5, y+h*0.5], [x+w*0.25, y+h*0.25],
        [x+w*0.75, y+h*0.25], [x+w*0.75, y+h*0.75], [x+w*0.25, y+h*0.75],
    ], dtype=np.float32)


def rmse(a, b):
    d = a[:min(len(a),len(b))] - b[:min(len(a),len(b))]
    return float(np.sqrt(np.mean(np.square(np.linalg.norm(d, axis=1)))))


def run_one(label, params):
    safe = label.replace(" ", "_")
    print(f"\n{'='*60}")
    print(f"  {label}  (yaw={params['yaw']})")
    print(f"{'='*60}")

    out_dir = OUT_DIR / safe; out_dir.mkdir(parents=True, exist_ok=True)
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
    sim_anchors = calib._last_sim_detections[board_cfg.board_id].ordered_points
    rmse_orig = rmse(sim_anchors, real_anchors)

    # Get template + content info
    ti = calib.custom_templates.get(board_cfg.board_id)
    tmpl = ti["template"]
    cb = ti.get("content_bbox", (0, 0, tmpl.shape[1], tmpl.shape[0]))
    c_x, c_y, c_w, c_h = [float(v) for v in cb]

    # From calibrator's match result, infer match_x, match_y
    # sim_anchors[0] = (match_x + c_x, match_y + c_y)
    calib_match_x = sim_anchors[0, 0] - c_x
    calib_match_y = sim_anchors[0, 1] - c_y
    print(f"  Calibrator: match=({calib_match_x:.0f},{calib_match_y:.0f}) "
          f"content=({c_x},{c_y},{c_w},{c_h}) RMSE={rmse_orig:.4f}")

    # ============ Local sub-pixel refinement ============
    # Match in a small window around calibrator's match position
    local_pad = 10
    lx = max(0, int(calib_match_x) - local_pad)
    ly = max(0, int(calib_match_y) - local_pad)
    lx1 = min(sim_eval.shape[1], int(calib_match_x) + tmpl.shape[1] + local_pad)
    ly1 = min(sim_eval.shape[0], int(calib_match_y) + tmpl.shape[0] + local_pad)
    local_search = sim_eval[ly:ly1, lx:lx1]

    r = cv2.matchTemplate(local_search, tmpl, cv2.TM_CCOEFF_NORMED)
    sx, sy = subpixel_peak(r)
    sub_match_x = lx + sx
    sub_match_y = ly + sy
    sub_anchors = anchors_9(sub_match_x + c_x, sub_match_y + c_y, c_w, c_h)
    rmse_sub = rmse(sub_anchors, real_anchors)

    print(f"  Local sub-pixel:")
    print(f"    Integer:  ({lx+sx:.0f},{ly+sy:.0f}) RMSE=?? (same as calibrator)")
    print(f"    Sub-1x:   ({sub_match_x:.4f},{sub_match_y:.4f}) RMSE={rmse_sub:.4f}")

    # ============ Upscale local search ============
    for scale, sl in [(2, "2x"), (4, "4x"), (8, "8x")]:
        s_big = cv2.resize(local_search, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        t_big = cv2.resize(tmpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        r_big = cv2.matchTemplate(s_big, t_big, cv2.TM_CCOEFF_NORMED)
        sx, sy = subpixel_peak(r_big)
        gx = lx + sx / scale
        gy = ly + sy / scale
        ga = anchors_9(gx + c_x, gy + c_y, c_w, c_h)
        rms = rmse(ga, real_anchors)
        locals()[f"rms_{sl}"] = rms
        print(f"    Sub-{sl}: anchor=({gx+c_x:.4f},{gy+c_y:.4f}) RMSE={rms:.4f}")

    return {"label": label, "rmse_orig": rmse_orig, "rmse_sub": rmse_sub,
            "rms_2x": locals().get("rms_2x", 0), "rms_4x": locals().get("rms_4x", 0),
            "rms_8x": locals().get("rms_8x", 0),
            "mx": calib_match_x, "my": calib_match_y}


results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r: results.append(r)
    time.sleep(1)

print(f"\n\n{'='*60}")
print(f"  SUMMARY — Local Sub-pixel Refinement")
print(f"{'='*60}")
if len(results) >= 2:
    cols = ["Label", "Orig", "Sub1x", "Sub2x", "Sub4x", "Sub8x", "Yaw"]
    hdr = "  " + "".join(f"{c:>12}" for c in cols)
    sep = "  " + "".join("-"*12 for _ in cols)
    print(f"\n{hdr}\n{sep}")
    for r in results:
        yaw = PARAM_SETS[r['label']]['yaw']
        print(f"  {r['label']:>12} {r['rmse_orig']:>12.6f} {r['rmse_sub']:>12.6f} "
              f"{r.get('rms_2x',0):>12.6f} {r.get('rms_4x',0):>12.6f} {r.get('rms_8x',0):>12.6f} "
              f"{yaw:>12.4f}")

    print(f"\n  Differentiation:")
    ref = results[0]
    for metric in ["rmse_orig", "rmse_sub", "rms_2x", "rms_4x", "rms_8x"]:
        deltas = []
        for r in results[1:]:
            v = r.get(metric, -1)
            if v > 0:
                deltas.append(abs(v - ref.get(metric, 0)))
        if deltas:
            max_d = max(deltas)
            avg_d = sum(deltas)/len(deltas)
            print(f"    {metric:<12}: avg Δ={avg_d:.6f}, max Δ={max_d:.6f}")

    print(f"\n  Sub-pixel match offsets:")
    for r in results:
        print(f"    {r['label']:<12}: calib=({r['mx']:.0f},{r['my']:.0f})")
