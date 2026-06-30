def _custom_board_geometric_penalty(
    self,
    board: BoardProfile,
    real_detection: DetectionResult,
    sim_detection: DetectionResult,
    sim_eval_image: Optional[np.ndarray],
) -> float:
    """Pure geometry — no pixel comparison, only homography structure checks.

    只使用 RANSAC 外点比例 + SVD 条件数，不依赖 sim_eval_image 内容。
    优点：轻量、不受光照纹理影响
    缺点：只覆盖 9 个锚点，非稠密
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

    outlier_frac = 1.0 - (inlier_count / n)

    A = np.array(H[:2, :2], dtype=np.float64)
    s = np.linalg.svd(A, compute_uv=False)
    cond = s.max() / max(s.min(), 1e-10)
    cond_penalty = max(0.0, (cond - 5.0)) * 0.3

    return outlier_frac * 5.0 + cond_penalty
