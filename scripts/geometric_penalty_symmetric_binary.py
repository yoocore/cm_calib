def _custom_board_geometric_penalty(
    self,
    board: BoardProfile,
    real_detection: DetectionResult,
    sim_detection: DetectionResult,
    sim_eval_image: Optional[np.ndarray],
) -> float:
    """Symmetric binary-structure penalty — both sides preprocessed identically.

    两侧经同一函数二值化后做 NCC + residual，加上几何项。
    优点：同域 0/255 比较，NCC 可靠，覆盖全标定板
    缺点：需要 sim_eval_image，计算量稍大
    """
    if sim_eval_image is None:
        return 0.0

    real_points = real_detection.ordered_points
    sim_points = sim_detection.ordered_points
    n = min(len(real_points), len(sim_points))
    if n < 4:
        return 0.0

    real_pts = np.ascontiguousarray(real_points[:n].astype(np.float32))
    sim_pts = np.ascontiguousarray(sim_points[:n].astype(np.float32))

    H, mask = cv2.findHomography(sim_pts, real_pts, cv2.RANSAC, 4.0)
    if H is None or mask is None:
        return 0.0

    inlier_mask = mask.ravel().astype(bool)
    inlier_count = int(inlier_mask.sum())
    if inlier_count < 4:
        return 0.0

    # ---- Geometric checks ----
    outlier_frac = 1.0 - (inlier_count / n)

    A = np.array(H[:2, :2], dtype=np.float64)
    s = np.linalg.svd(A, compute_uv=False)
    cond = s.max() / max(s.min(), 1e-10)
    cond_penalty = max(0.0, (cond - 5.0)) * 0.3

    # ---- Symmetric binary-structure comparison ----
    real_bbox = self._points_bbox(real_points)
    rx, ry, rw, rh = real_bbox
    if rw < 12 or rh < 12:
        return outlier_frac * 5.0 + cond_penalty

    real_patch = self.real_img[ry : ry + rh, rx : rx + rw]
    if real_patch.size == 0:
        return outlier_frac * 5.0 + cond_penalty

    # warpPerspective 内部做逆，传 H (sim->real) 即可
    full_warped = cv2.warpPerspective(
        sim_eval_image, H, (sim_eval_image.shape[1], sim_eval_image.shape[0]),
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    warped = full_warped[ry : ry + rh, rx : rx + rw]

    def _to_gray(img):
        return img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    real_gray = _to_gray(real_patch)
    sim_gray = _to_gray(warped)

    # 同一函数二值化，两侧对称
    real_processed = self._preprocess_template_match_image(real_gray, board)
    sim_processed = self._preprocess_template_match_image(sim_gray, board)

    real_f32 = real_processed.astype(np.float32)
    sim_f32 = sim_processed.astype(np.float32)

    r_mean = np.mean(real_f32)
    s_mean = np.mean(sim_f32)
    numer = float(np.mean((real_f32 - r_mean) * (sim_f32 - s_mean)))
    r_var = float(np.mean(np.square(real_f32 - r_mean)))
    s_var = float(np.mean(np.square(sim_f32 - s_mean)))
    denom = float(np.sqrt(max(1e-10, r_var * s_var)))
    ncc = numer / max(1e-10, denom)
    ncc = max(-1.0, min(1.0, ncc))

    residual_rms = float(np.sqrt(np.mean(np.square(real_f32 - sim_f32)))) / 255.0

    structure_penalty = max(0.0, (1.0 - ncc) * 15.0)
    residual_penalty = residual_rms * 20.0

    return (
        outlier_frac * 5.0
        + cond_penalty
        + structure_penalty
        + residual_penalty
    )
