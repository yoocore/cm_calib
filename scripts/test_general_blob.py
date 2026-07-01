"""Test: general-purpose blob centroid residual (board-type agnostic).

Idea: For ANY board pattern, detect all connected components (white blobs)
in both SIM and real ROI via adaptive thresholding. Match by nearest
neighbor. Residual RMS = geometric distortion measure.

No assumptions about: number of dots, grid size, pattern shape.
"""
import cv2, numpy as np, json, sys, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
OUT_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/general_blob_test")
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


def detect_all_blobs(gray, method="otsu", min_area=8, max_area=5000):
    """Detect ALL bright blobs as gray-weighted centroids.
    No assumptions about count or arrangement.

    Methods:
      otsu: Otsu threshold (adaptive per image)
      adaptive: local adaptive thresholding
      fixed_100: fixed threshold at 100
      fixed_128: fixed threshold at 128
    """
    if method == "otsu":
        _, bin = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif method == "adaptive":
        bin = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 31, 2)
    elif method.startswith("fixed_"):
        t = int(method.split("_")[1])
        _, bin = cv2.threshold(gray, t, 255, cv2.THRESH_BINARY)
    elif method == "otsu_inv":
        _, bin = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    elif method == "triangle":
        _, bin = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_TRIANGLE)
    else:
        _, bin = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)

    num, labels, stats, centroids = cv2.connectedComponentsWithStats(bin, connectivity=8)
    blobs = []
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
        blobs.append((cx, cy, area))
    return blobs, bin


def match_blobs(src_blobs, dst_blobs, max_dist=60):
    """Match each source blob to nearest destination blob (no duplicate)."""
    if not src_blobs or not dst_blobs:
        return []
    dst_arr = np.array([[d[0], d[1]] for d in dst_blobs])
    matches = []
    used_dst = set()
    for si, (sx, sy, _) in enumerate(src_blobs):
        dists = np.sqrt((dst_arr[:, 0] - sx)**2 + (dst_arr[:, 1] - sy)**2)
        min_idx = int(np.argmin(dists))
        min_dist = float(dists[min_idx])
        if min_dist < max_dist and min_idx not in used_dst:
            matches.append((si, min_idx, min_dist))
            used_dst.add(min_idx)
    return matches


def compute_residual(src_blobs, dst_blobs, matches, mode="raw"):
    """Compute residual RMS after removing bulk motion."""
    if len(matches) < 4:
        return 0.0

    src_pts = np.array([[src_blobs[si][0], src_blobs[si][1]] for si, _, _ in matches], dtype=np.float32)
    dst_pts = np.array([[dst_blobs[ri][0], dst_blobs[ri][1]] for _, ri, _ in matches], dtype=np.float32)

    errors = np.linalg.norm(src_pts - dst_pts, axis=1)
    rms_raw = float(np.sqrt(np.mean(np.square(errors))))

    if mode == "translation" or len(matches) >= 3:
        mean_disp = np.mean(src_pts - dst_pts, axis=0)
        corrected = src_pts - mean_disp
        errors_t = np.linalg.norm(corrected - dst_pts, axis=1)
        rms_trans = float(np.sqrt(np.mean(np.square(errors_t))))
    else:
        rms_trans = rms_raw

    if mode == "affine" and len(matches) >= 3:
        A = np.ones((len(src_pts), 3), dtype=np.float32)
        A[:, :2] = src_pts
        cx, _, _, _ = np.linalg.lstsq(A, dst_pts[:, 0], rcond=None)
        cy, _, _, _ = np.linalg.lstsq(A, dst_pts[:, 1], rcond=None)
        pred = np.column_stack([A @ cx, A @ cy])
        errors_a = np.linalg.norm(pred - dst_pts, axis=1)
        rms_affine = float(np.sqrt(np.mean(np.square(errors_a))))
    else:
        rms_affine = rms_trans

    return rms_raw, rms_trans, rms_affine


METHODS = ["otsu", "triangle", "fixed_100", "fixed_128", "adaptive", "otsu_inv"]


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

    x, y, w, h = ROI
    real_roi = real_img[y:y+h, x:x+w]
    sim_roi = sim_eval[y:y+h, x:x+w]

    board_score = total_detail.board_scores[0]
    print(f"  Existing: RMSE={board_score.rmse:.3f} Score={total_detail.total_score:.4f}")

    results_row = {"label": label,
                   "rmse_existing": board_score.rmse,
                   "score_existing": total_detail.total_score}

    # Detect blobs: try REAL with low threshold, SIM with high threshold
    # Then also try same method for both
    combos = [
        ("real_otsu", "sim_otsu"),
        ("real_fixed100", "sim_fixed240"),
        ("real_triangle", "sim_triangle"),
        ("real_adaptive", "sim_adaptive"),
        ("real_otsu_inv", "sim_otsu"),
    ]

    print(f"\n  Blob centroid residuals:")
    print(f"  {'Method':<24} {'Real':>5} {'SIM':>5} {'Match':>6} {'RawRMS':>8} {'TransRMS':>8} {'AffineRMS':>8}")
    print(f"  {'-'*24} {'-'*5} {'-'*5} {'-'*6} {'-'*8} {'-'*8} {'-'*8}")

    for real_method, sim_method in combos:
        # Real: lower threshold
        real_blobs, real_bin = detect_all_blobs(real_roi, method=real_method.split("_")[0] + "_" + real_method.split("_")[1])
        # SIM try thresholds
        sim_blobs_list = []
        if "fixed" in sim_method:
            t = int(sim_method.split("fixed")[1])
            sim_blobs, sim_bin = detect_all_blobs(sim_roi, method=f"fixed_{t}")
        else:
            sim_blobs, sim_bin = detect_all_blobs(sim_roi, method=sim_method.split("_")[0] + "_" + sim_method.split("_")[1])

        matches = match_blobs(sim_blobs, real_blobs)
        if len(matches) < 4:
            print(f"  {real_method}+{sim_method:<9} {len(real_blobs):>5} {len(sim_blobs):>5} {len(matches):>6} {'N/A':>8} {'N/A':>8} {'N/A':>8}")
            continue

        rr, rt, ra = compute_residual(sim_blobs, real_blobs, matches)

        print(f"  {real_method}+{sim_method:<9} {len(real_blobs):>5} {len(sim_blobs):>5} {len(matches):>6} {rr:>8.4f} {rt:>8.4f} {ra:>8.4f}")
        results_row[f"rr_{real_method}_{sim_method}"] = rr
        results_row[f"rt_{real_method}_{sim_method}"] = rt
        results_row[f"ra_{real_method}_{sim_method}"] = ra
        results_row[f"cnt_{real_method}_{sim_method}"] = len(matches)

    return results_row


all_results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r: all_results.append(r)
    time.sleep(1)

print(f"\n\n{'='*60}")
print(f"  SUMMARY — General Blob Centroid Residual")
print(f"{'='*60}")
if len(all_results) >= 2:
    # Find all metric keys
    metric_keys = [k for k in all_results[0].keys()
                   if k.startswith("rr_") or k.startswith("rt_")]

    for metric_type, label_s in [("rr_", "RawRMS"), ("rt_", "TransRMS")]:
        print(f"\n  {label_s} differentiation:")
        relevant = [k for k in metric_keys if k.startswith(metric_type)]
        for key in relevant:
            vals = [r.get(key, -1) for r in all_results if r.get(key, -1) > 0]
            if len(vals) < 3:
                continue
            deltas = [abs(v - vals[0]) for v in vals[1:]]
            avg_d = sum(deltas)/len(deltas)
            method_short = key[len(metric_type):]
            print(f"    {method_short:<30}: {[f'{v:.4f}' for v in vals]}  avg Δ={avg_d:.4f}")

    # Best raw RMS differentiation
    print(f"\n  Best metric by differentiation:")
    best_delta = 0
    best_key = ""
    for key in metric_keys:
        vals = [r.get(key, -1) for r in all_results if r.get(key, -1) > 0]
        if len(vals) < 3:
            continue
        deltas = [abs(v - vals[0]) for v in vals[1:]]
        avg_d = sum(deltas)/len(deltas) if deltas else 0
        if avg_d > best_delta:
            best_delta = avg_d
            best_key = key
    if best_key:
        vals = [r.get(best_key, 0) for r in all_results]
        print(f"    {best_key}: {[f'{v:.4f}' for v in vals]}")
        print(f"    avg Δ = {best_delta:.4f}")
