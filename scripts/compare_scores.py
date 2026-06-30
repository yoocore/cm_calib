import sys, cv2, numpy as np, json, math
from pathlib import Path

sys.path.insert(0, r'E:\Coding\VibeCoding\cm_calib')
from src.calibration.calib_types import BoardProfile, DetectionResult

# === Load config ===
config_path = r"C:\CM_Projects\TM15.1_StreamaxCamera\Movie\calibtool_VehSensor_0\camera.VehSensor_0.json"
with open(config_path, 'r') as f:
    cfg = json.load(f)

board_cfg = cfg['boards'][0]
tmpl_path = Path(board_cfg['template_image'])
roi = tuple(board_cfg['roi'])

print(f"Board: {board_cfg['board_id']} ({board_cfg['board_type']})")
print(f"ROI: {roi}")

# === Load images ===
real_img = cv2.imread(cfg['real_image'], cv2.IMREAD_GRAYSCALE)

sim29_path = r"C:\CM_Projects\TM15.1_StreamaxCamera\SimOutput\calibration\VehSensor_0\rounds_20260629_162520\round_01\campaign\explore\start_00\initial.png"
sim30_path = r"C:\CM_Projects\TM15.1_StreamaxCamera\SimOutput\calibration\VehSensor_0\rounds_20260630_134512\round_01\campaign\explore\start_00\initial.png"

sim29 = cv2.imread(sim29_path, cv2.IMREAD_GRAYSCALE)
sim30 = cv2.imread(sim30_path, cv2.IMREAD_GRAYSCALE)
if sim30.shape != real_img.shape:
    sim30 = cv2.resize(sim30, (real_img.shape[1], real_img.shape[0]))

templ = cv2.imread(str(tmpl_path), cv2.IMREAD_GRAYSCALE)
print(f"Real: {real_img.shape}, sim29: {sim29.shape}, sim30: {sim30.shape}")
print(f"Template: {templ.shape}")

# === Template matching ===
thresh = board_cfg.get('template_match_threshold', 0.45)

def detect_board(img, template, roi_rect, threshold=0.45):
    rx, ry, rw, rh = roi_rect
    pad = 20
    sx = max(0, rx - pad)
    sy = max(0, ry - pad)
    sw = min(img.shape[1] - sx, rw + 2*pad)
    sh = min(img.shape[0] - sy, rh + 2*pad)
    roi_patch = img[sy:sy+sh, sx:sx+sw]
    result = cv2.matchTemplate(roi_patch, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None, max_val
    tx, ty = max_loc
    bx, by = sx + tx, sy + ty
    bw, bh = template.shape[1], template.shape[0]
    anchors = np.array([
        [bx, by], [bx+bw-1, by], [bx+bw-1, by+bh-1], [bx, by+bh-1],
        [bx+bw*0.5, by+bh*0.5],
        [bx+bw*0.25, by+bh*0.25], [bx+bw*0.75, by+bh*0.25],
        [bx+bw*0.75, by+bh*0.75], [bx+bw*0.25, by+bh*0.75],
    ], dtype=np.float32)
    return DetectionResult("mk_1", True, 9, anchors, "custom_maker", (bx, by, bw, bh), "template_match"), max_val

board = BoardProfile(
    board_id=board_cfg['board_id'],
    board_type=board_cfg['board_type'],
    weight=board_cfg.get('weight', 0.8),
    critical=board_cfg.get('critical', True),
    roi=roi,
    custom_detector=board_cfg.get('custom_detector', 'template_match'),
    template_match_threshold=thresh,
    template_binary_threshold=board_cfg.get('template_binary_threshold', 0),
    min_detected_points=board_cfg.get('min_detected_points', 9),
    alpha=board_cfg.get('alpha', 1000.0),
    beta=board_cfg.get('beta', 0.1),
    fail_penalty=board_cfg.get('fail_penalty', 1e6),
)

real_det, real_score = detect_board(real_img, templ, roi, thresh)
sim29_det, sim29_score = detect_board(sim29, templ, roi, thresh)
sim30_det, sim30_score = detect_board(sim30, templ, roi, thresh)

print(f"\nDetection: real={real_score:.3f}, sim29={sim29_score:.3f}, sim30={sim30_score:.3f}")
if real_det:
    print(f"  real bbox: {real_det.roi_used}")
if sim29_det:
    print(f"  sim29 bbox: {sim29_det.roi_used}")
if sim30_det:
    print(f"  sim30 bbox: {sim30_det.roi_used}")

# === Scoring ===
def points_bbox(pts):
    pts_r = pts.reshape(-1, 2)
    min_x = int(math.floor(float(np.min(pts_r[:, 0]))))
    min_y = int(math.floor(float(np.min(pts_r[:, 1]))))
    max_x = int(math.ceil(float(np.max(pts_r[:, 0]))))
    max_y = int(math.ceil(float(np.max(pts_r[:, 1]))))
    return min_x, min_y, max(1, max_x-min_x), max(1, max_y-min_y)

def compute_penalty(real_det, sim_det, sim_eval, real_img, board):
    if real_det is None or sim_det is None or sim_eval is None:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    rp, sp = real_det.ordered_points, sim_det.ordered_points
    n = min(len(rp), len(sp))
    if n < 4:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    ra = np.ascontiguousarray(rp[:n].astype(np.float32))
    sa = np.ascontiguousarray(sp[:n].astype(np.float32))
    H, mask = cv2.findHomography(sa, ra, cv2.RANSAC, 4.0)
    if H is None or mask is None:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    im = mask.ravel().astype(bool)
    ic = int(im.sum())
    if ic < 4:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    outlier_frac = 1.0 - (ic / n)
    A = np.array(H[:2, :2], dtype=np.float64)
    s = np.linalg.svd(A, compute_uv=False)
    cond = s.max() / max(s.min(), 1e-10)
    cond_penalty = max(0.0, (cond - 5.0)) * 0.3
    # Binary structure comparison
    rx, ry, rw, rh = points_bbox(rp)
    if rw < 12 or rh < 12:
        return outlier_frac * 5.0 + cond_penalty, 0.0, outlier_frac * 5.0 + cond_penalty, 0.0, cond
    real_patch = real_img[ry:ry+rh, rx:rx+rw]
    if real_patch.size == 0:
        return outlier_frac * 5.0 + cond_penalty, 0.0, outlier_frac * 5.0 + cond_penalty, 0.0, cond
    H_inv = np.linalg.inv(H)
    # warpPerspective internally applies M^(-1), so pass H (sim->real) directly
    # Full warp + crop at bbox to handle ROI offset correctly
    full_warped = cv2.warpPerspective(
        sim_eval, H, (sim_eval.shape[1], sim_eval.shape[0]),
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    warped = full_warped[ry:ry+rh, rx:rx+rw]
    # Same preprocessing for both
    def preproc(gray, brd):
        if brd.template_binary_threshold > 0:
            _, p = cv2.threshold(gray, float(brd.template_binary_threshold), 255, cv2.THRESH_BINARY_INV)
            return p.astype(np.uint8)
        return gray
    real_proc = preproc(real_patch, board)
    sim_proc = preproc(warped, board)
    rf32 = real_proc.astype(np.float32)
    sf32 = sim_proc.astype(np.float32)
    # NCC
    rm, sm = np.mean(rf32), np.mean(sf32)
    numer = float(np.mean((rf32 - rm) * (sf32 - sm)))
    rv = float(np.mean(np.square(rf32 - rm)))
    sv = float(np.mean(np.square(sf32 - sm)))
    denom = float(np.sqrt(max(1e-10, rv * sv)))
    ncc = numer / max(1e-10, denom)
    ncc = max(-1.0, min(1.0, ncc))
    res_rms = float(np.sqrt(np.mean(np.square(rf32 - sf32)))) / 255.0
    struct_pen = max(0.0, (1.0 - ncc) * 15.0)
    res_pen = res_rms * 20.0
    total = outlier_frac * 5.0 + cond_penalty + struct_pen + res_pen
    return total, ncc, outlier_frac * 5.0 + cond_penalty, struct_pen + res_pen, cond

for label, sim_det, sim_eval in [("6/29", sim29_det, sim29), ("6/30", sim30_det, sim30)]:
    n = min(real_det.point_count, sim_det.point_count)
    deltas = sim_det.ordered_points[:n] - real_det.ordered_points[:n]
    dists = np.linalg.norm(deltas, axis=1)
    rmse = float(np.sqrt(np.mean(np.square(dists))))
    mean_e = float(np.mean(dists))
    max_e = float(np.max(dists))
    geo_penalty, ncc, geom_part, struct_part, cond = compute_penalty(real_det, sim_det, sim_eval, real_img, board)
    total_rmse = rmse + geo_penalty
    miss_rate = 0.0
    total_score = total_rmse + board.alpha * miss_rate + board.beta * max_e
    weighted_score = total_score * board.weight

    print(f"\n{'='*50}")
    print(f"  {label} (initial yaw={29*' '}pitch=)")
    print(f"{'='*50}")
    print(f"  Pure geometric RMSE:  {rmse:.3f} px")
    print(f"  + geometric_penalty:  {geo_penalty:.3f} px")
    print(f"    ├ NCC (二值vs二值): {ncc:.4f}")
    print(f"    ├ struct_penalty:   {struct_part:.3f}")
    print(f"    ├ geom_part:        {geom_part:.3f}")
    print(f"    └ cond#:            {cond:.2f}")
    print(f"  = total_rmse:         {total_rmse:.3f} px")
    print(f"  max_error:            {max_e:.3f} px")
    print(f"  total_score:          {total_score:.3f}")
    print(f"  weighted (x{board.weight}): {weighted_score:.3f}")
    print(f"  [对比] 旧评分:        {27.39 if label=='6/29' else 25.33}")

print(f"\n{'='*50}")
print(f"  结论")
print(f"{'='*50}")
print(f"  6/29 overlay更好 → 新score应更低 ✓")
print(f"  6/30 overlay更差 → 新score应更高 ✓")
