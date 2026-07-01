"""Test: preprocess real image ROI before template extraction.

Hypothesis:
  Currently the template is extracted from raw real image (blurry, mid-gray).
  SIM renders crisp near-binary dots. If we preprocess the real ROI to
  look more like SIM (sharper, higher contrast), the template match should
  be more precise, potentially giving differentiable RMSE.

What this tests:
  1. CLAHE (adaptive histogram equalization) on ROI
  2. Unsharp mask (sharpen)
  3. Binarization (threshold at 128/150)
  4. CLAHE + sharpen combo
  5. Gamma correction (darken bg, lighten dots)

  Compare template appearance and RMSE differentiation across 3 param sets.
"""
import cv2, numpy as np, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
OUT_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/enhance_template_test")
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

ENHANCE_METHODS = [
    "none",
    "clahe",
    "sharpen",
    "clahe+sharpen",
    "binarize_128",
    "gamma_0.5",
]


def enhance_roi(roi_gray: np.ndarray, method: str) -> np.ndarray:
    """Apply preprocessing to ROI to make it look more like SIM rendering."""
    if method == "none":
        return roi_gray.copy()

    elif method == "clahe":
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        return clahe.apply(roi_gray)

    elif method == "sharpen":
        blurred = cv2.GaussianBlur(roi_gray, (0, 0), sigmaX=1.0)
        sharpened = cv2.addWeighted(roi_gray, 1.6, blurred, -0.6, 0)
        return sharpened

    elif method == "clahe+sharpen":
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        eq = clahe.apply(roi_gray)
        blurred = cv2.GaussianBlur(eq, (0, 0), sigmaX=1.0)
        sharpened = cv2.addWeighted(eq, 1.6, blurred, -0.6, 0)
        return sharpened

    elif method == "binarize_128":
        _, bin = cv2.threshold(roi_gray, 128, 255, cv2.THRESH_BINARY)
        return bin

    elif method.startswith("gamma_"):
        gamma = float(method.split("_")[1])
        look = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)], dtype=np.uint8)
        return cv2.LUT(roi_gray, look)

    return roi_gray.copy()


def rmse_between(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    deltas = a[:n] - b[:n]
    return float(np.sqrt(np.mean(np.square(np.linalg.norm(deltas, axis=1)))))


def detect_anchors_from_template(gray, template, roi_bbox):
    """Template matching, return 9 anchor points."""
    x, y, w, h = roi_bbox
    pad = 40
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(gray.shape[1], x + w + pad)
    y1 = min(gray.shape[0], y + h + pad)
    search = gray[y0:y1, x0:x1]

    result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(result)
    mx = x0 + max_loc[0]
    my = y0 + max_loc[1]

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


def run_one(label, params):
    safe = label.replace(" ", "_")
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    out_dir = OUT_DIR / safe
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load config and run calibrator to get SIM render
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    temp_cfg = _cfg_with_initial_values(cfg, params)
    temp_cfg["output_dir"] = str(out_dir)
    calib = CameraCalibrator(temp_cfg, config_path=CONFIG_PATH)
    calib._apply_initial_value_map_with_retry(params, label)
    calib.cfg["output_dir"] = str(out_dir)

    total_detail, sim_path = calib.evaluate("initial", baseline_metrics=None)

    # 2. Get images
    real_img = calib.real_img
    raw_sim = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
    sim_eval = calib._prepare_eval_image(raw_sim)

    # 3. Real detection anchors (geometry-based, reference)
    board_cfg = calib.boards[0]
    real_anchors = calib.real_detections[board_cfg.board_id].ordered_points

    # 4. Calibrator's own result
    sim_anchors_orig = calib._last_sim_detections[board_cfg.board_id].ordered_points
    rmse_orig = rmse_between(sim_anchors_orig, real_anchors)

    # 5. Crop template from real image ROI
    x, y, w, h = ROI
    roi_gray = real_img[y:y+h, x:x+w]

    print(f"  Existing calibration:")
    print(f"    RMSE={rmse_orig:.3f}, Score={total_detail.total_score:.4f}")
    print(f"    Template size: {roi_gray.shape[1]}x{roi_gray.shape[0]}")
    print(f"    Template range: {roi_gray.min()}-{roi_gray.max()}")

    # 6. Test each enhancement method
    print(f"\n  {'Method':<22} {'RMSE':>8} {'ΔvsOrig':>10} {'Status':>10}")
    print(f"  {'-'*22} {'-'*8} {'-'*10} {'-'*10}")
    print(f"  {'original(calib)':<22} {rmse_orig:>8.3f} {'---':>10} {'ref':>10}")

    results_row = {"label": label, "rmse_orig": rmse_orig}

    for method in ENHANCE_METHODS:
        enhanced = enhance_roi(roi_gray, method)

        # Save enhanced template for inspection
        cv2.imwrite(str(out_dir / f"template_{method.replace('+','_')}.png"), enhanced)

        # Template matching on SIM eval image
        anchors = detect_anchors_from_template(sim_eval, enhanced, ROI)
        rmse_enhanced = rmse_between(anchors, real_anchors)
        delta = rmse_enhanced - rmse_orig

        if rmse_enhanced < rmse_orig - 0.1:
            status = "BETTER"
        elif rmse_enhanced > rmse_orig + 0.1:
            status = "WORSE"
        else:
            status = "~same"

        print(f"  {method:<22} {rmse_enhanced:>8.3f} {delta:>+10.3f} {status:>10}")
        results_row[f"rmse_{method.replace('+','_')}"] = rmse_enhanced

    # 7. Also test NCC between enhanced template and SIM board
    print(f"\n  NCC (template vs SIM board):")
    sim_board = sim_eval[y:y+h, x:x+w]
    for method in ENHANCE_METHODS:
        enhanced = enhance_roi(roi_gray, method)
        a = enhanced.astype(np.float32)
        b = sim_board.astype(np.float32)
        a_m, b_m = np.mean(a), np.mean(b)
        n = np.mean((a - a_m) * (b - b_m))
        d = max(1e-10, np.sqrt(max(1e-10, np.mean(np.square(a - a_m)) * np.mean(np.square(b - b_m)))))
        ncc = n / d
        print(f"    {method:<22} NCC={ncc:.4f}")

    return results_row


results = []
for label, params in PARAM_SETS.items():
    r = run_one(label, params)
    if r:
        results.append(r)
    time.sleep(1)

# Summary
print(f"\n\n{'='*60}")
print(f"  SUMMARY — Real ROI Enhancement Effect on Template Matching")
print(f"{'='*60}")
if len(results) >= 2:
    cols = ["Label"] + [f"rmse_{m.replace('+','_')}" for m in ENHANCE_METHODS]
    hdr = "  " + "".join(f"{c:>16}" for c in cols)
    sep = "  " + "".join("-"*16 for _ in cols)
    print(f"\n{hdr}\n{sep}")
    for r in results:
        line = f"  {r['label']:>16}"
        for c in cols[1:]:
            line += f" {r.get(c, 0):>16.3f}"
        print(line)

    ref = results[0]
    # Differentiation: max delta from ref for each method
    print(f"\n  Differentiation (max Δ from {ref['label']}):")
    orig_delta = 0
    for c in cols[1:]:
        deltas = [abs(r.get(c, 0) - ref.get(c, 0)) for r in results[1:]]
        max_delta = max(deltas) if deltas else 0
        if c == "rmse_orig":
            orig_delta = max_delta
        method_name = c.replace("rmse_", "").replace("_", "+")
        print(f"    {method_name:<22}: max Δ = {max_delta:.4f}")

    # Best method
    best_delta = 0
    best_method = ""
    for c in cols[1:]:
        deltas = [abs(r.get(c, 0) - ref.get(c, 0)) for r in results[1:]]
        max_delta = max(deltas) if deltas else 0
        if max_delta > best_delta:
            best_delta = max_delta
            best_method = c.replace("rmse_", "")
    print(f"\n  Original differentiation: max Δ = {orig_delta:.4f}")
    print(f"  Best method:             {best_method} (max Δ = {best_delta:.4f})")
    if best_delta > orig_delta * 1.2:
        print(f"  -> Enhancement IMPROVES differentiation!")
    else:
        print(f"  -> Enhancement does NOT significantly improve differentiation")
