"""Test geometric approaches that could differentiate param sets.

Key facts:
- Template == real board crop (extracted from real image)
- SIM: only board on plain bg, real: board + complex bg
- SIM board NCC with real board = 0.4 (different look, same structure)
- Board ROI: 497x253 pixels at (673, 405)

Tests:
1. Edge overlap within board ROI (SIM vs real Canny edges)
2. Phase correlation sub-pixel shift within ROI
3. ORB feature matching within ROI
"""
import cv2, numpy as np, json, sys, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path(
    "C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0"
    "/camera.VehSensor_0.json"
)
TEMP_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/geom_test")
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

ROI = (673, 405, 497, 253)  # x, y, w, h


def canny_overlap(img_a, img_b, low=30, high=100):
    """Fraction of edge pixels that coincide (within 2px)."""
    ea = cv2.Canny(img_a, low, high)
    eb = cv2.Canny(img_b, low, high)
    if ea.sum() == 0 or eb.sum() == 0:
        return 0.0, 0.0, 0.0
    # Dilate edges to allow 2px tolerance
    k = np.ones((5, 5), np.uint8)
    eb_dil = cv2.dilate(eb, k, iterations=1)
    overlap = float(np.logical_and(ea > 0, eb_dil > 0).sum())
    prec = overlap / max(1, float(ea.sum()))
    rec = overlap / max(1, float(eb.sum()))
    f1 = 2 * prec * rec / max(1e-10, prec + rec)
    return f1, prec, rec


def phase_correlation_shift(img_a, img_b, upsample=10):
    """Sub-pixel shift via phase correlation."""
    # Both should be same size, float32
    a = img_a.astype(np.float32)
    b = img_b.astype(np.float32)
    # Window to reduce edge artifacts
    h, w = a.shape
    han = np.outer(np.hanning(h), np.hanning(w))
    a_w = a * han
    b_w = b * han
    # FFT
    fa = np.fft.fft2(a_w)
    fb = np.fft.fft2(b_w)
    cross = fa * np.conj(fb)
    cross_abs = np.abs(cross) + 1e-10
    pc = cross / cross_abs  # normalized cross-power spectrum
    # IFFT
    result = np.fft.ifft2(pc).real
    # Upsample around peak
    max_loc = np.unravel_index(np.argmax(result), result.shape)
    # Simple parabolic sub-pixel refinement
    cy, cx = max_loc
    if 1 <= cy < h - 1 and 1 <= cx < w - 1:
        dx = (result[cy, cx + 1] - result[cy, cx - 1]) / (2 * (2 * result[cy, cx] - result[cy, cx + 1] - result[cy, cx - 1]))
        dy = (result[cy + 1, cx] - result[cy - 1, cx]) / (2 * (2 * result[cy, cx] - result[cy + 1, cx] - result[cy - 1, cx]))
    else:
        dx = dy = 0.0
    # Convert to pixel shift
    shift_x = (cx + dx) if cx <= w // 2 else (cx + dx - w)
    shift_y = (cy + dy) if cy <= h // 2 else (cy + dy - h)
    peak_val = float(result[cy, cx])
    return shift_x, shift_y, peak_val


def orb_matching_residual(img_a, img_b):
    """ORB feature matching within board region. Return RMS residual."""
    orb = cv2.ORB_create(nfeatures=2000,
                         scaleFactor=1.2, nlevels=8,
                         edgeThreshold=7,
                         patchSize=31)
    kp_a, des_a = orb.detectAndCompute(img_a, None)
    kp_b, des_b = orb.detectAndCompute(img_b, None)
    if des_a is None or des_b is None or len(kp_a) < 4 or len(kp_b) < 4:
        return 0.0, 0, 0, 0
    # FLANN matcher for floating-point descriptors converted from binary
    # Use brute-force Hamming for ORB
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des_a, des_b)
    if len(matches) < 4:
        return 0.0, 0, 0, 0
    # Sort by distance
    matches = sorted(matches, key=lambda m: m.distance)
    # Use top matches
    n_use = min(50, len(matches))
    good = matches[:n_use]
    pts_a = np.float32([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_b = np.float32([kp_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    # Homography + residual
    H, mask = cv2.findHomography(pts_a, pts_b, cv2.RANSAC, 3.0)
    if H is None:
        return 0.0, 0, 0, 0
    inliers = mask.ravel().astype(bool).sum()
    # Project pts_a to pts_b via H
    projected = cv2.perspectiveTransform(pts_a, H)
    errors = np.linalg.norm(projected.reshape(-1, 2) - pts_b.reshape(-1, 2), axis=1)
    rms = float(np.sqrt(np.mean(errors[mask.ravel().astype(bool)] ** 2)))
    return rms, int(inliers), len(matches), len(kp_a) + len(kp_b)


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
    real_img = calib.real_img  # grayscale
    raw_sim = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
    sim_eval = calib._prepare_eval_image(raw_sim)  # same size as real_img

    # Crop board ROI from both
    x, y, w, h = ROI
    real_board = real_img[y:y+h, x:x+w]
    sim_board = sim_eval[y:y+h, x:x+w]

    # 1. Edge overlap
    f1_edge, prec_edge, rec_edge = canny_overlap(real_board, sim_board)
    # Try multiple Canny thresholds
    f1_best = 0
    best_thresh = (0, 0)
    for low in [20, 30, 50, 80]:
        for high in [80, 100, 150, 200]:
            f1, _, _ = canny_overlap(real_board, sim_board, low, high)
            if f1 > f1_best:
                f1_best = f1
                best_thresh = (low, high)

    # 2. Phase correlation
    sx, sy, peak = phase_correlation_shift(real_board, sim_board)
    shift_mag = math.sqrt(sx*sx + sy*sy)

    # 3. ORB matching
    orb_rms, orb_inl, orb_matches, orb_total = orb_matching_residual(real_board, sim_board)

    print(f"  RMSE (existing):      {total_detail.board_scores[0].rmse:.3f}")
    print(f"  Score (existing):     {total_detail.total_score:.4f}")
    print(f"")
    print(f"  [New metrics - board ROI {w}x{h}]")
    print(f"  Edge overlap (best F1) : {f1_best:.4f}  (thresh={best_thresh})")
    print(f"  Edge overlap (fixed 30/100 F1): {f1_edge:.4f}")
    print(f"    precision: {prec_edge:.4f}, recall: {rec_edge:.4f}")
    print(f"  Phase correlation:")
    print(f"    shift: ({sx:.3f}, {sy:.3f}) px, mag={shift_mag:.3f} px, peak={peak:.4f}")
    print(f"  ORB matching:")
    print(f"    RMS residual: {orb_rms:.3f} px, inliers={orb_inl}, matches={orb_matches}, features={orb_total}")

    return {
        "label": label,
        "rmse": total_detail.board_scores[0].rmse,
        "score": total_detail.total_score,
        "edge_f1": f1_best,
        "edge_f1_fixed": f1_edge,
        "pc_shift": shift_mag,
        "pc_sx": sx,
        "pc_sy": sy,
        "pc_peak": peak,
        "orb_rms": orb_rms,
        "orb_inl": orb_inl,
    }


results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r:
        results.append(r)
    time.sleep(1)

# Summary
print(f"\n\n{'='*60}")
print(f"  SUMMARY — New Geometric Metrics")
print(f"{'='*60}")
if len(results) >= 2:
    headers = ["Label", "RMSE", "Score", "EdgeF1", "PC_shift", "ORB_rms", "ORB_inl"]
    print(f"  {'|'.join(f'{h:>14}' for h in headers)}")
    print(f"  {'|'.join('-'*14 for _ in headers)}")
    for r in results:
        vals = [r['label'], f"{r['rmse']:.3f}", f"{r['score']:.4f}",
                f"{r['edge_f1']:.4f}", f"{r['pc_shift']:.3f}",
                f"{r['orb_rms']:.3f}", f"{r['orb_inl']}"]
        print(f"  {'|'.join(f'{v:>14}' for v in vals)}")

    # Check differentiation
    ref = results[0]
    for r in results[1:]:
        print(f"\n  {r['label']} vs {ref['label']}:")
        print(f"    ΔRMSE:     {abs(r['rmse'] - ref['rmse']):.4f}")
        print(f"    ΔEdgeF1:   {abs(r['edge_f1'] - ref['edge_f1']):.4f}")
        print(f"    ΔPC_shift: {abs(r['pc_shift'] - ref['pc_shift']):.4f}")
        print(f"    ΔORB_rms:  {abs(r['orb_rms'] - ref['orb_rms']):.4f}")

    best_delta = 0
    best_metric = ""
    for metric in ['edge_f1', 'pc_shift', 'orb_rms']:
        deltas = [abs(r[metric] - ref[metric]) for r in results[1:]]
        avg_delta = sum(deltas) / len(deltas)
        if avg_delta > best_delta:
            best_delta = avg_delta
            best_metric = metric

    thresh = 0.03 if best_metric == 'edge_f1' else 0.5
    meaningful = best_delta > thresh
    print(f"\n  Best metric: {best_metric} (avg Δ={best_delta:.4f})")
    if meaningful:
        print(f"  ✅ {best_metric} shows differentiation!")
    else:
        print(f"  ❌ No metric shows reliable differentiation (noise level)")
