"""Test: dot residual after removing bulk transform.

Key insight from analysis:
  Template matching finds board at integer pixel position → RMSE ~1.0 for ALL params.
  Dot centroids show sub-pixel differences, but RMS includes whole-board translation.
  After subtracting the bulk transform (translation/affine), residual = pure distortion.

  This residual should be MORE sensitive to perspective changes (yaw/pitch) than raw dot RMS.
"""
import cv2, numpy as np, json, sys, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
TEMP_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/dot_residual_test")
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

ROI_X, ROI_Y, ROI_W, ROI_H = (673, 405, 497, 253)

def detect_dots(gray, threshold=100, min_area=10, max_area=2000):
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
        weights = gray_f[mask]
        total_w = weights.sum()
        if total_w > 0:
            cx = float((xs * weights).sum() / total_w)
            cy = float((ys * weights).sum() / total_w)
        else:
            cx, cy = float(centroids[i][0]), float(centroids[i][1])
        dots.append((cx, cy, area))
    return dots

def match_dots(sim_dots, real_dots, max_dist=50):
    """Match each SIM dot to nearest real dot."""
    if not sim_dots or not real_dots:
        return [], []
    real_arr = np.array([[d[0], d[1]] for d in real_dots])
    matches = []
    used_real = set()
    for si, (sx, sy, _) in enumerate(sim_dots):
        dists = np.sqrt((real_arr[:, 0] - sx)**2 + (real_arr[:, 1] - sy)**2)
        min_idx = int(np.argmin(dists))
        min_dist = float(dists[min_idx])
        if min_dist < max_dist and min_idx not in used_real:
            matches.append((si, min_idx, min_dist))
            used_real.add(min_idx)
    return matches

def compute_residual(sim_dots, real_dots, matches, mode="raw"):
    """
    mode="raw": RMS of all dot displacements (includes bulk translation)
    mode="translation": subtract mean displacement, then RMS
    mode="affine": best-fit affine transform, residual RMS
    """
    if len(matches) < 4:
        return 0.0, 0.0, 0.0

    sim_pts = np.array([[sim_dots[si][0], sim_dots[si][1]] for si, _, _ in matches], dtype=np.float32)
    real_pts = np.array([[real_dots[ri][0], real_dots[ri][1]] for _, ri, _ in matches], dtype=np.float32)
    errors = np.linalg.norm(sim_pts - real_pts, axis=1)
    rms_raw = float(np.sqrt(np.mean(np.square(errors))))

    # Translation-only residual: subtract mean displacement
    mean_disp = np.mean(sim_pts - real_pts, axis=0)
    corrected = sim_pts - mean_disp
    errors_t = np.linalg.norm(corrected - real_pts, axis=1)
    rms_trans = float(np.sqrt(np.mean(np.square(errors_t))))

    # Affine residual: best-fit 2x2 + translation
    if len(matches) >= 3:
        A = np.ones((len(sim_pts), 3), dtype=np.float32)
        A[:, :2] = sim_pts
        coeff_x, _, _, _ = np.linalg.lstsq(A, real_pts[:, 0], rcond=None)
        coeff_y, _, _, _ = np.linalg.lstsq(A, real_pts[:, 1], rcond=None)
        pred_x = A @ coeff_x
        pred_y = A @ coeff_y
        pred = np.column_stack([pred_x, pred_y])
        errors_a = np.linalg.norm(pred - real_pts, axis=1)
        rms_affine = float(np.sqrt(np.mean(np.square(errors_a))))
    else:
        rms_affine = rms_trans

    return rms_raw, rms_trans, rms_affine


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
    board_score = total_detail.board_scores[0]

    real_img = calib.real_img
    raw_sim = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
    sim_eval = calib._prepare_eval_image(raw_sim)

    # Crop board ROI
    x, y, w, h = ROI_X, ROI_Y, ROI_W, ROI_H
    real_board = real_img[y:y+h, x:x+w]
    sim_board = sim_eval[y:y+h, x:x+w]

    # Detect dots
    real_dots = detect_dots(real_board, threshold=100)
    # Find best SIM threshold
    sim_dots = []
    for st in [250, 245, 240, 235, 230]:
        sd = detect_dots(sim_board, threshold=st)
        if len(sd) >= 25:
            sim_dots = sd
            break
    if not sim_dots:
        sim_dots = detect_dots(sim_board, threshold=220)

    # Match
    matches = match_dots(sim_dots, real_dots)

    # Existing geometric penalty
    geom_penalty = 0.0
    n = min(len(calib.real_detections[calib.boards[0].board_id].ordered_points),
            len(calib._last_sim_detections[calib.boards[0].board_id].ordered_points))
    if n >= 4:
        real_pts = calib.real_detections[calib.boards[0].board_id].ordered_points[:n]
        sim_pts = calib._last_sim_detections[calib.boards[0].board_id].ordered_points[:n]
        H, mask = cv2.findHomography(sim_pts, real_pts, cv2.RANSAC, 4.0)
        if H is not None and mask is not None:
            outlier_frac = 1.0 - (mask.sum() / n)
            A = np.array(H[:2, :2], dtype=np.float64)
            s = np.linalg.svd(A, compute_uv=False)
            cond = s.max() / max(s.min(), 1e-10)
            cond_penalty = max(0.0, (cond - 5.0)) * 0.3
            geom_penalty = outlier_frac * 5.0 + cond_penalty

    # Dot residuals
    rms_raw, rms_trans, rms_affine = compute_residual(sim_dots, real_dots, matches)

    print(f"  Existing calibration:")
    print(f"    RMSE: {board_score.rmse:.3f}, geom_penalty: {geom_penalty:.3f}")
    print(f"    Score: {total_detail.total_score:.4f}")
    print(f"")
    print(f"  Dot-based metrics ({len(matches)} matched dots):")
    print(f"    Raw dot RMS:       {rms_raw:.4f} px")
    print(f"    Translation-resid: {rms_trans:.4f} px")
    print(f"    Affine-resid:      {rms_affine:.4f} px")

    # Analyze: X vs Y components
    if matches:
        sim_pts = np.array([[sim_dots[si][0], sim_dots[si][1]] for si, _, _ in matches])
        real_pts = np.array([[real_dots[ri][0], real_dots[ri][1]] for _, ri, _ in matches])
        disp = sim_pts - real_pts
        mean_x, mean_y = np.mean(disp, axis=0)
        print(f"    Mean displacement: X={mean_x:+.3f}, Y={mean_y:+.3f}")
        # Directional analysis: x-error vs dot column position
        # Dots should show x-shift gradient across the board if yaw is off
        # Sort dots by their x position (column) and check
        sorted_by_x = real_pts[np.argsort(real_pts[:, 0])]
        if len(sorted_by_x) >= 10:
            left5_x = np.mean(disp[np.argsort(real_pts[:, 0])[:5], 0])
            right5_x = np.mean(disp[np.argsort(real_pts[:, 0])[-5:], 0])
            print(f"    X-displacement gradient: left={left5_x:+.3f}px, right={right5_x:+.3f}px, diff={right5_x-left5_x:+.3f}px")

    return {
        "label": label,
        "rmse": board_score.rmse,
        "score": total_detail.total_score,
        "geom_penalty": geom_penalty,
        "matched": len(matches),
        "rms_raw": rms_raw,
        "rms_trans": rms_trans,
        "rms_affine": rms_affine,
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
print(f"  SUMMARY — Dot Residual Analysis")
print(f"{'='*60}")
if len(results) >= 2:
    cols = ["Label", "RMSE", "Score", "GeomPen", "Matched", "RawRMS", "TransRes", "AffineRes"]
    hdr = "  " + "".join(f"{c:>12}" for c in cols)
    sep = "  " + "".join("-"*12 for _ in cols)
    print(f"\n{hdr}\n{sep}")
    for r in results:
        print(f"  {r['label']:>12} {r['rmse']:>12.3f} {r['score']:>12.4f} {r['geom_penalty']:>12.3f} "
              f"{r['matched']:>12} {r['rms_raw']:>12.3f} {r['rms_trans']:>12.3f} {r['rms_affine']:>12.3f}")

    ref = results[0]
    print(f"\n  Differentiation (delta from {ref['label']}):")
    for r in results[1:]:
        print(f"  {r['label']}:")
        for metric in ['rmse', 'geom_penalty', 'rms_raw', 'rms_trans', 'rms_affine']:
            delta = abs(r[metric] - ref[metric])
            marker = " *" if delta > 0.05 else ""
            print(f"    {metric:<20}: {r[metric]:.4f}  (d={delta:.4f}){marker}")

    # Best metric
    metrics = ['rmse', 'geom_penalty', 'rms_raw', 'rms_trans', 'rms_affine']
    print(f"\n  Best differentiating metric:")
    best_delta = 0
    best_metric = ""
    for metric in metrics:
        deltas = [abs(r[metric] - ref[metric]) for r in results[1:]]
        avg_delta = sum(deltas) / len(deltas)
        print(f"    {metric:<20}: avg delta = {avg_delta:.4f}")
        if avg_delta > best_delta:
            best_delta = avg_delta
            best_metric = metric
    print(f"\n  Winner: {best_metric} (avg delta = {best_delta:.4f})")
