"""Test: white-dot centroid detection & matching between SIM and real.

Board: custom_maker, 30 white dots (5 rows × 6 cols).

Key insight:
- SIM rendering: dots near max brightness (254-255) → detect at THRESH_BINARY thresh=254
- Real photo: dots at moderate brightness (peaks ~200) → detect at THRESH_BINARY thresh=100
- Match dots between SIM and real by spatial proximity
- Compute RMS of positional differences = geometric error
"""
import cv2, numpy as np, json, sys, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
TEMP_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/dot_test")
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

ROI = (673, 405, 497, 253)

# Tunable thresholds
REAL_THRESH = 100   # real image: detect dots at this threshold
SIM_THRESH = 250    # SIM image: detect dots at this threshold (very bright)
MIN_AREA = 10
MAX_AREA = 2000
MATCH_DIST = 50     # max pixel distance for matching SIM dot to real dot

def detect_dots(gray, threshold, min_area=MIN_AREA, max_area=MAX_AREA):
    """Gray-weighted centroid detection for white dots."""
    _, bin = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    num, labels, stats, centroids = cv2.connectedComponentsWithStats(bin, connectivity=8)
    dots = []
    gray_f = gray.astype(np.float32)
    for i in range(1, num):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_area or area > max_area:
            continue
        mask = (labels == i)
        ys, xs = np.where(mask)
        if len(xs) < 3:
            continue
        # Gray-weighted centroid
        weights = gray_f[mask]
        total_w = weights.sum()
        if total_w > 0:
            cx = float((xs * weights).sum() / total_w)
            cy = float((ys * weights).sum() / total_w)
        else:
            cx, cy = float(centroids[i][0]), float(centroids[i][1])
        dots.append((cx, cy, area))
    return dots, bin

def match_dots(sim_dots, real_dots, max_dist=MATCH_DIST):
    """Match each SIM dot to nearest real dot by spatial proximity."""
    if not sim_dots or not real_dots:
        return [], []

    real_arr = np.array([[d[0], d[1]] for d in real_dots])
    matches = []  # (sim_idx, real_idx, distance)
    used_real = set()

    for si, (sx, sy, _) in enumerate(sim_dots):
        dists = np.sqrt((real_arr[:, 0] - sx)**2 + (real_arr[:, 1] - sy)**2)
        min_idx = int(np.argmin(dists))
        min_dist = float(dists[min_idx])
        if min_dist < max_dist and min_idx not in used_real:
            matches.append((si, min_idx, min_dist))
            used_real.add(min_idx)

    return matches, list(used_real)

def compute_dot_rms(sim_dots, real_dots, matches):
    """RMS of positional differences between matched dots."""
    if len(matches) < 4:
        return 0.0, []

    errors = []
    for si, ri, _ in matches:
        sx, sy, _ = sim_dots[si]
        rx, ry, _ = real_dots[ri]
        err = math.sqrt((sx - rx)**2 + (sy - ry)**2)
        errors.append(err)

    rms = math.sqrt(sum(e*e for e in errors) / len(errors))
    return rms, errors


def run_one(label, params):
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

    # Images
    real_img = calib.real_img
    raw_sim = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
    sim_eval = calib._prepare_eval_image(raw_sim)

    # Crop board ROI
    x, y, w, h = ROI
    real_board = real_img[y:y+h, x:x+w]
    sim_board = sim_eval[y:y+h, x:x+w]

    # Detect dots
    real_dots, real_bin = detect_dots(real_board, REAL_THRESH)

    # Try multiple SIM thresholds to find the one giving ~30 dots
    sim_dots = []
    for st in [250, 245, 240, 235, 230, 220, 200]:
        sd, sb = detect_dots(sim_board, st)
        if len(sd) >= 25:
            sim_dots = sd
            sim_bin = sb
            break

    # Match
    matches, used_real = match_dots(sim_dots, real_dots)
    rms, errors = compute_dot_rms(sim_dots, real_dots, matches)

    # Existing scoring
    board_score = total_detail.board_scores[0]

    print(f"  Existing: RMSE={board_score.rmse:.3f}, Score={total_detail.total_score:.4f}")
    print(f"")
    print(f"  [White-dot centroid matching]")
    print(f"  Real dots detected: {len(real_dots)} (thresh={REAL_THRESH})")
    print(f"  SIM dots detected:  {len(sim_dots)}")
    print(f"  Matched:            {len(matches)} pairs")
    print(f"  Dot RMS error:      {rms:.4f} px")
    if errors:
        print(f"  Error range:        {min(errors):.2f} - {max(errors):.2f} px")
        print(f"  Error >3px:         {sum(1 for e in errors if e > 3)}/{len(errors)}")
        print(f"  Median error:       {sorted(errors)[len(errors)//2]:.2f} px")

    # Show matched pairs
    if len(matches) > 0:
        print(f"  Top matches (first 5):")
        for i, (si, ri, d) in enumerate(matches[:5]):
            sx, sy, _ = sim_dots[si]
            rx, ry, _ = real_dots[ri]
            print(f"    #{i}: SIM({sx:.2f},{sy:.2f}) → Real({rx:.2f},{ry:.2f}) Δ={d:.2f}px")

    return {
        "label": label,
        "rmse": board_score.rmse,
        "score": total_detail.total_score,
        "real_dots": len(real_dots),
        "sim_dots": len(sim_dots),
        "matched": len(matches),
        "dot_rms": rms,
        "dot_errors": errors,
        "params": params,
    }


results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r:
        results.append(r)
    time.sleep(1)

# Summary
print(f"\n\n{'='*60}")
print(f"  SUMMARY — White-dot Centroid Matching")
print(f"{'='*60}")
if len(results) >= 2:
    print(f"  {'Label':<20} {'RMSE':>7} {'Score':>8} {'RealDots':>9} {'SimDots':>8} {'Matched':>8} {'DotRMS':>8}")
    print(f"  {'-'*20} {'-'*7} {'-'*8} {'-'*9} {'-'*8} {'-'*8} {'-'*8}")
    for r in results:
        print(f"  {r['label']:<20} {r['rmse']:>7.3f} {r['score']:>8.4f} {r['real_dots']:>9} {r['sim_dots']:>8} {r['matched']:>8} {r['dot_rms']:>8.3f}")

    ref = results[0]
    print(f"\n  Differentiation analysis (vs {ref['label']}):")
    for r in results[1:]:
        delta_rms = abs(r['dot_rms'] - ref['dot_rms'])
        print(f"    {r['label']}: ΔDotRMS = {delta_rms:.3f} px {'✅' if delta_rms > 0.3 else '❌'}")

    # If dot RMS shows promise, suggest integration
    max_delta = max(abs(r['dot_rms'] - ref['dot_rms']) for r in results[1:])
    if max_delta > 0.3:
        print(f"\n  ✅ Dot centroid method differentiates params (max Δ={max_delta:.3f}px)!")
    else:
        print(f"\n  ❌ Dot centroid still can't differentiate (max Δ={max_delta:.3f}px)")
