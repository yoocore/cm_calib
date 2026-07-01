"""Test: histogram matching + blur on SIM to improve detection accuracy.

Hypothesis:
  SIM renders board as sharp binary-like image (0/255), while real image
  has blur + continuous grayscale. If we match SIM's appearance to real
  (histogram match + mild blur), the template match could be more precise.

What this tests:
  1. Does histogram matching improve SIM vs real detection consistency?
  2. Does it amplify the RMSE differentiation between param sets?
  3. Is it worth integrating into the scoring pipeline?
"""
import cv2, numpy as np, json, sys, time, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
TEMP_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/hist_match_test")
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


def histogram_match(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Match histogram of source to target. Both uint8 grayscale."""
    src = source.ravel().astype(np.float32)
    tgt = target.ravel().astype(np.float32)

    src_sorted = np.sort(src)
    tgt_sorted = np.sort(tgt)

    src_indices = np.argsort(src)
    mapped = np.empty_like(src)
    mapped[src_indices] = np.interp(
        np.arange(len(src)),
        np.arange(len(tgt)),
        tgt_sorted,
    )
    result = mapped.reshape(source.shape).astype(np.uint8)
    return result


def apply_sim_postprocess(sim_gray: np.ndarray, real_gray: np.ndarray,
                          method: str = "hist_match") -> np.ndarray:
    """Apply post-processing to SIM image to make it look more like real."""
    result = sim_gray.copy()

    if method == "hist_match":
        # Global histogram matching
        result = histogram_match(sim_gray, real_gray)

    elif method == "blur_only":
        # Just mild Gaussian blur
        blurred = cv2.GaussianBlur(sim_gray, (0, 0), sigmaX=1.5)
        result = blurred

    elif method == "blur_then_hist":
        # Blur first (smooth edge aliasing), then histogram match
        blurred = cv2.GaussianBlur(sim_gray, (0, 0), sigmaX=1.5)
        result = histogram_match(blurred, real_gray)

    elif method == "bilateral_then_hist":
        # Bilateral filter (smooth flat regions, preserve edges), then hist match
        filtered = cv2.bilateralFilter(sim_gray, d=9, sigmaColor=30, sigmaSpace=3)
        result = histogram_match(filtered, real_gray)

    elif method == "eq_first":
        # Equalize SIM first to spread its narrow histogram, then match
        eq = cv2.equalizeHist(sim_gray)
        result = histogram_match(eq, real_gray)

    elif method == "adaptive_mid":
        # Map midtones specifically — SIM has mostly 0/255, real has mid-grays
        # Apply a smooth mapping: 0→0, 255→real_mean*1.5, with curve in between
        real_mean = float(np.mean(real_gray))
        look = np.zeros(256, dtype=np.uint8)
        for i in range(256):
            v = i / 255.0
            mapped = v * v * (real_mean * 2.0 / 255.0) * 255.0
            look[i] = max(0, min(255, int(round(mapped))))
        result = cv2.LUT(sim_gray, look)

    return result


def detect_9_anchors(gray: np.ndarray, template: np.ndarray,
                     roi_bbox, method=cv2.TM_CCOEFF_NORMED) -> np.ndarray:
    """Simple template matching, return 9 anchor points."""
    # Search within expanded ROI
    x, y, w, h = roi_bbox
    pad = 40
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(gray.shape[1], x + w + pad)
    y1 = min(gray.shape[0], y + h + pad)
    search = gray[y0:y1, x0:x1]

    if search.shape[0] < template.shape[0] or search.shape[1] < template.shape[1]:
        # Fallback to whole image
        search = gray
        x0, y0 = 0, 0

    result = cv2.matchTemplate(search, template, method)
    _, _, _, max_loc = cv2.minMaxLoc(result)
    mx = x0 + max_loc[0]
    my = y0 + max_loc[1]

    # 9 anchor points from bounding box
    tw, th = template.shape[1], template.shape[0]
    anchors = np.array([
        [mx, my],
        [mx + tw - 1, my],
        [mx + tw - 1, my + th - 1],
        [mx, my + th - 1],
        [mx + tw * 0.5, my + th * 0.5],
        [mx + tw * 0.25, my + th * 0.25],
        [mx + tw * 0.75, my + th * 0.25],
        [mx + tw * 0.75, my + th * 0.75],
        [mx + tw * 0.25, my + th * 0.75],
    ], dtype=np.float32)
    return anchors


def rmse_between(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    deltas = a[:n] - b[:n]
    return float(np.sqrt(np.mean(np.square(np.linalg.norm(deltas, axis=1)))))


METHODS = ["none", "hist_match", "blur_then_hist", "blur_only",
           "bilateral_then_hist", "eq_first"]

def run_one(label, params):
    print(f"\n{'='*65}")
    print(f"  {label}")
    print(f"{'='*65}")

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

    real_img = calib.real_img
    raw_sim = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
    sim_eval = calib._prepare_eval_image(raw_sim)

    # Board area for histogram reference
    rx, ry, rw, rh = ROI
    real_board = real_img[ry:ry+rh, rx:rx+rw]
    sim_board = sim_eval[ry:ry+rh, rx:rx+rw]

    # Load detection template
    board_cfg = calib.boards[0]
    template_info = calib.custom_templates.get(board_cfg.board_id)
    if template_info is None:
        print("  ERROR: no template info")
        return None
    template_gray = template_info["template"]

    # Real detection (baseline)
    real_anchors = calib.real_detections[board_cfg.board_id].ordered_points

    # SIM detection under each post-processing method
    sim_detections = {}
    for method in METHODS:
        if method == "none":
            processed = sim_eval
        else:
            processed = apply_sim_postprocess(sim_eval, real_img, method)

        anchors = detect_9_anchors(processed, template_gray, ROI)
        sim_detections[method] = anchors

        # Save debug images
        if method != "none":
            cv2.imwrite(str(out_dir / f"sim_{method}.png"), processed)

    # Evaluation anchor (baseline)
    sim_anchors_orig = calib._last_sim_detections[board_cfg.board_id].ordered_points
    rmse_orig = rmse_between(sim_anchors_orig, real_anchors)

    print(f"  {'Method':<22} {'RMSE':>8} {'ΔvsOrig':>8} {'ΔvsReal':>8} {'Status':>10}")
    print(f"  {'-'*22} {'-'*8} {'-'*8} {'-'*8} {'-'*10}")
    print(f"  {'original(calib)':<22} {rmse_orig:>8.3f} {'---':>8} {'---':>8} {'ref':>10}")

    results_row = {"label": label, "rmse_orig": rmse_orig}
    for method in METHODS:
        anchors = sim_detections[method]
        rmse_sim = rmse_between(anchors, real_anchors)
        rmse_vs_orig = rmse_between(anchors, sim_anchors_orig)
        delta = rmse_sim - rmse_orig
        status = "BETTER" if delta < -0.1 else ("WORSE" if delta > 0.1 else "~same")
        print(f"  {method:<22} {rmse_sim:>8.3f} {rmse_vs_orig:>8.3f} {delta:>+8.3f} {status:>10}")
        results_row[f"rmse_{method}"] = rmse_sim

    # Also compare the post-processed SIM NCC with real (as a sanity check)
    print(f"  NCC SIM vs real board patch:")
    for method in METHODS:
        if method == "none":
            proc_board = sim_board
        else:
            proc_board = apply_sim_postprocess(sim_board, real_board, method)
        # NCC
        rf = real_board.astype(np.float32)
        sf = proc_board.astype(np.float32)
        r_mean = np.mean(rf)
        s_mean = np.mean(sf)
        numer = np.mean((rf - r_mean) * (sf - s_mean))
        r_var = np.mean(np.square(rf - r_mean))
        s_var = np.mean(np.square(sf - s_mean))
        ncc = numer / max(1e-10, math.sqrt(max(1e-10, r_var * s_var)))
        print(f"    {method:<22} NCC={ncc:.4f}")

    return results_row


results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r:
        results.append(r)
    time.sleep(1)

# Summary
print(f"\n\n{'='*65}")
print(f"  SUMMARY — Histogram Matching Effect on SIM Detection")
print(f"{'='*65}")
if len(results) >= 2:
    print(f"\n  RMSE comparison (lower = better match to real):")
    header = f"  {'Label':<16}"
    for m in METHODS:
        header += f" {m:>16}"
    print(header)
    print(f"  {'-'*16}" + ''.join('-'*17 for _ in METHODS))
    for r in results:
        line = f"  {r['label']:<16}"
        for m in METHODS:
            key = f"rmse_{m}"
            if key in r:
                line += f" {r[key]:>16.3f}"
        print(line)

    # Best method for each param set
    print(f"\n  Best method per param set:")
    for r in results:
        best_m = min(METHODS, key=lambda m: r.get(f"rmse_{m}", 999))
        best_v = r.get(f"rmse_{best_m}", 0)
        print(f"    {r['label']}: {best_m} (RMSE={best_v:.3f})")

    # Check differentiation: best method vs original
    print(f"\n  Differentiation (max ΔRMSE between param sets):")
    ref = results[0]
    for method in METHODS:
        vals = [abs(r.get(f"rmse_{method}", 0) - ref.get(f"rmse_{method}", 0))
                for r in results[1:]]
        max_delta = max(vals) if vals else 0
        orig_delta = max(abs(r['rmse_orig'] - ref['rmse_orig']) for r in results[1:])
        print(f"    {method:<22}: Δmax={max_delta:.3f}  (original: {orig_delta:.3f})")

    # Check if ANY method beats original for differentiation
    best_delta = 0
    best_method = ""
    for method in METHODS:
        vals = [abs(r.get(f"rmse_{method}", 0) - ref.get(f"rmse_{method}", 0))
                for r in results[1:]]
        max_delta = max(vals) if vals else 0
        if max_delta > best_delta:
            best_delta = max_delta
            best_method = method
    orig_delta = max(abs(r['rmse_orig'] - ref['rmse_orig']) for r in results[1:])
    print(f"\n  Original differentiation: Δmax={orig_delta:.3f}")
    if best_delta > orig_delta * 1.2:
        print(f"  ✅ {best_method} improves differentiation (Δ={best_delta:.3f} vs {orig_delta:.3f})")
    else:
        print(f"  ❌ No method significantly improves differentiation (best: {best_method}={best_delta:.3f})")
