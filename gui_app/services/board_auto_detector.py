from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np


@dataclass
class DetectedTag:
    tag_id: int
    corners: np.ndarray
    center: Tuple[float, float]
    family: str = ""


@dataclass
class TagGrid:
    grid_id: str
    tags: List[DetectedTag]
    bbox: Tuple[int, int, int, int]
    center: Tuple[float, float]
    rows: int = 0
    cols: int = 0


@dataclass
class DetectedBoard:
    board_type: str
    bbox: Tuple[int, int, int, int]
    corners: np.ndarray
    board_id: str = ""
    board_size: Optional[Tuple[int, int]] = None
    tags: List[DetectedTag] = field(default_factory=list)
    center: Tuple[float, float] = (0.0, 0.0)
    area: float = 0.0
    weight: float = 1.0
    template_image: Optional[str] = None


_COMMON_CHECKERBOARD_SIZES = [
    (7, 4), (4, 7),
    (9, 6), (6, 9),
    (8, 5), (5, 8),
    (11, 8), (8, 11),
    (12, 8), (8, 12),
]


def _bbox_from_points(points: np.ndarray, padding_ratio: float = 0.15) -> Tuple[int, int, int, int]:
    if points.size == 0:
        return (0, 0, 0, 0)
    pts = points.reshape(-1, 2)
    x_min, y_min = pts.min(axis=0)
    x_max, y_max = pts.max(axis=0)
    w = x_max - x_min
    h = y_max - y_min
    pad_x = max(4, int(round(w * padding_ratio)))
    pad_y = max(4, int(round(h * padding_ratio)))
    return (
        int(max(0, x_min - pad_x)),
        int(max(0, y_min - pad_y)),
        int(w + 2 * pad_x),
        int(h + 2 * pad_y),
    )


def _center_from_bbox(bbox: Tuple[int, int, int, int]) -> Tuple[float, float]:
    x, y, w, h = bbox
    return (x + w / 2.0, y + h / 2.0)


def _area_from_bbox(bbox: Tuple[int, int, int, int]) -> float:
    return float(bbox[2] * bbox[3])


def _bbox_iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    inter_x1, inter_y1 = max(ax1, bx1), max(ay1, by1)
    inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)
    inter_area = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)
    a_area = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    b_area = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = a_area + b_area - inter_area
    return inter_area / union if union > 0 else 0.0


def _deduplicate_boards(
    boards: List[DetectedBoard], iou_threshold: float = 0.4
) -> List[DetectedBoard]:
    if not boards:
        return []
    boards.sort(key=lambda b: b.area, reverse=True)
    kept: List[DetectedBoard] = []
    for board in boards:
        if any(_bbox_iou(board.bbox, k.bbox) >= iou_threshold for k in kept):
            continue
        kept.append(board)
    return kept


class BoardAutoDetector:

    def detect_checkerboards(
        self,
        image: np.ndarray,
        board_sizes: Optional[Sequence[Tuple[int, int]]] = None,
    ) -> List[DetectedBoard]:
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        sizes_to_try = list(board_sizes) if board_sizes else list(_COMMON_CHECKERBOARD_SIZES)
        all_boards: List[DetectedBoard] = []

        for size in sizes_to_try:
            cols, rows = size
            found, corners = cv2.findChessboardCorners(
                gray, (cols, rows),
                cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE,
            )
            if not found or corners is None:
                continue
            corners = corners.reshape(-1, 2).astype(np.float32)
            bbox = _bbox_from_points(corners)
            all_boards.append(
                DetectedBoard(
                    board_type="checkerboard",
                    bbox=bbox,
                    corners=corners,
                    board_size=(cols, rows),
                    center=_center_from_bbox(bbox),
                    area=_area_from_bbox(bbox),
                )
            )

        return _deduplicate_boards(all_boards)

    def detect_checkerboard_instances(
        self,
        image: np.ndarray,
        board_sizes: Optional[Sequence[Tuple[int, int]]] = None,
    ) -> List[DetectedBoard]:
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        sizes_to_try = list(board_sizes) if board_sizes else list(_COMMON_CHECKERBOARD_SIZES)
        all_boards: List[DetectedBoard] = []
        search_mask = np.full(gray.shape[:2], 255, dtype=np.uint8)

        for size in sizes_to_try:
            cols, rows = size
            working = gray.copy()
            max_instances = 20
            for _ in range(max_instances):
                found, corners = cv2.findChessboardCorners(
                    working, (cols, rows),
                    cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE,
                )
                if not found or corners is None:
                    break
                pts = corners.reshape(-1, 2).astype(np.float32)
                bbox = _bbox_from_points(pts)

                is_dup = any(_bbox_iou(bbox, b.bbox) >= 0.3 for b in all_boards)
                if is_dup:
                    x, y, w, h = bbox
                    search_mask[max(0, y):y + h, max(0, x):x + w] = 0
                    working = gray.copy()
                    working[search_mask == 0] = 0
                    continue

                all_boards.append(
                    DetectedBoard(
                        board_type="checkerboard",
                        bbox=bbox,
                        corners=pts,
                        board_size=(cols, rows),
                        center=_center_from_bbox(bbox),
                        area=_area_from_bbox(bbox),
                    )
                )
                x, y, w, h = bbox
                search_mask[max(0, y):y + h, max(0, x):x + w] = 0
                working = gray.copy()
                working[search_mask == 0] = 0

        return _deduplicate_boards(all_boards)

    def detect_aruco_tags(
        self,
        image: np.ndarray,
        dictionary: str = "DICT_4X4_50",
    ) -> List[DetectedTag]:
        if not hasattr(cv2, "aruco"):
            raise RuntimeError("OpenCV aruco module is unavailable")

        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        dict_id = getattr(cv2.aruco, dictionary, None)
        if dict_id is None:
            raise ValueError(f"Unsupported ArUco dictionary: {dictionary}")
        aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)
        detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())

        marker_corners, marker_ids, _ = detector.detectMarkers(gray)
        if marker_ids is None or not marker_corners:
            return []

        tags: List[DetectedTag] = []
        for idx, corners in enumerate(marker_corners):
            tag_id = int(marker_ids[idx].flatten()[0])
            pts = corners.reshape(-1, 2).astype(np.float32)
            center = tuple(pts.mean(axis=0).tolist())
            tags.append(DetectedTag(
                tag_id=tag_id,
                corners=pts,
                center=(float(center[0]), float(center[1])),
                family=dictionary,
            ))
        return tags

    def detect_apriltags(
        self,
        image: np.ndarray,
        tag_family: str = "tagStandard41h12",
        auto_family: bool = False,
    ) -> List[DetectedTag]:
        try:
            from pupil_apriltags import Detector
        except ImportError:
            raise RuntimeError(
                "pupil_apriltags is not installed. Install with: pip install pupil_apriltags"
            )

        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        family_candidates = (
            ["tagStandard41h12", "tag36h11", "tag25h9", "tag16h5"]
            if auto_family
            else [tag_family]
        )

        for family in family_candidates:
            detector = Detector(families=family)
            results = detector.detect(gray)
            if not results:
                continue

            tags: List[DetectedTag] = []
            for det in results:
                pts = np.asarray(det.corners, dtype=np.float32).reshape(-1, 2)
                center = tuple(pts.mean(axis=0).tolist())
                tags.append(DetectedTag(
                    tag_id=int(det.tag_id),
                    corners=pts,
                    center=(float(center[0]), float(center[1])),
                    family=family,
                ))
            return tags

        return []

    def detect_charuco_boards(
        self,
        image: np.ndarray,
        board_size: Tuple[int, int],
        dictionary: str = "DICT_4X4_50",
        marker_length_ratio: float = 0.7,
        square_size: float = 1.0,
    ) -> List[DetectedBoard]:
        if not hasattr(cv2, "aruco"):
            raise RuntimeError("OpenCV aruco module is unavailable")

        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        dict_id = getattr(cv2.aruco, dictionary, None)
        if dict_id is None:
            raise ValueError(f"Unsupported ArUco dictionary: {dictionary}")
        aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)
        marker_length = square_size * marker_length_ratio
        charuco_board = cv2.aruco.CharucoBoard(
            board_size, square_size, marker_length, aruco_dict,
        )
        detector = cv2.aruco.CharucoDetector(charuco_board)

        all_boards: List[DetectedBoard] = []
        charuco_corners, charuco_ids, marker_corners, marker_ids = (
            detector.detectBoard(gray)
        )
        if charuco_corners is None or len(charuco_corners) == 0:
            return []

        pts = charuco_corners.reshape(-1, 2).astype(np.float32)
        bbox = _bbox_from_points(pts)
        all_boards.append(
            DetectedBoard(
                board_type="charuco",
                bbox=bbox,
                corners=pts,
                board_size=board_size,
                center=_center_from_bbox(bbox),
                area=_area_from_bbox(bbox),
            )
        )
        return all_boards

    def detect_circle_grids(
        self,
        image: np.ndarray,
        board_sizes: Optional[Sequence[Tuple[int, int]]] = None,
        grid_type: str = "symmetric",
    ) -> List[DetectedBoard]:
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        is_asymmetric = grid_type.strip().lower() == "asymmetric"
        flags = cv2.CALIB_CB_ASYMMETRIC_GRID if is_asymmetric else cv2.CALIB_CB_SYMMETRIC_GRID
        sizes_to_try = list(board_sizes) if board_sizes else [
            (5, 5), (7, 7), (9, 6), (6, 9), (11, 8), (8, 11), (4, 11), (11, 4),
        ]

        all_boards: List[DetectedBoard] = []
        for size in sizes_to_try:
            cols, rows = size
            found, centers = cv2.findCirclesGrid(gray, (cols, rows), None, flags)
            if not found or centers is None:
                continue
            pts = centers.reshape(-1, 2).astype(np.float32)
            bbox = _bbox_from_points(pts)
            is_dup = any(_bbox_iou(bbox, b.bbox) >= 0.3 for b in all_boards)
            if is_dup:
                continue
            all_boards.append(
                DetectedBoard(
                    board_type="circle_grid",
                    bbox=bbox,
                    corners=pts,
                    board_size=(cols, rows),
                    center=_center_from_bbox(bbox),
                    area=_area_from_bbox(bbox),
                )
            )
        return _deduplicate_boards(all_boards)

    def detect_aruco_grids(
        self,
        image: np.ndarray,
        dictionary: str = "DICT_4X4_50",
        board_sizes: Optional[Sequence[Tuple[int, int]]] = None,
    ) -> List[DetectedBoard]:
        if not hasattr(cv2, "aruco"):
            raise RuntimeError("OpenCV aruco module is unavailable")

        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        dict_id = getattr(cv2.aruco, dictionary, None)
        if dict_id is None:
            raise ValueError(f"Unsupported ArUco dictionary: {dictionary}")
        aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)
        detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())

        marker_corners, marker_ids, _ = detector.detectMarkers(gray)
        if marker_ids is None or not marker_corners:
            return []

        sizes_to_try = list(board_sizes) if board_sizes else [
            (3, 3), (4, 3), (3, 4), (5, 3), (3, 5), (4, 4), (5, 5),
        ]

        all_boards: List[DetectedBoard] = []
        for size in sizes_to_try:
            cols, rows = size
            marker_length = 1.0
            marker_separation = 0.5
            grid_board = cv2.aruco.GridBoard(
                (cols, rows), marker_length, marker_separation, aruco_dict,
            )
            obj_pts, img_pts = grid_board.matchImagePoints(marker_corners, marker_ids)
            if obj_pts is None or len(obj_pts) == 0:
                continue
            pts = np.asarray(img_pts, dtype=np.float32).reshape(-1, 2)
            bbox = _bbox_from_points(pts)
            is_dup = any(_bbox_iou(bbox, b.bbox) >= 0.3 for b in all_boards)
            if is_dup:
                continue
            all_boards.append(
                DetectedBoard(
                    board_type="aruco_grid",
                    bbox=bbox,
                    corners=pts,
                    board_size=(cols, rows),
                    center=_center_from_bbox(bbox),
                    area=_area_from_bbox(bbox),
                )
            )
        return _deduplicate_boards(all_boards)


def group_tags_into_grids(
    tags: List[DetectedTag],
    distance_threshold: Optional[float] = None,
) -> List[TagGrid]:
    if not tags:
        return []

    n = len(tags)
    centers = np.array([t.center for t in tags], dtype=np.float64)

    if distance_threshold is None and n > 1:
        edges: List[Tuple[float, int, int]] = []
        for i in range(n):
            for j in range(i + 1, n):
                dist = float(np.linalg.norm(centers[i] - centers[j]))
                edges.append((dist, i, j))
        edges.sort()

        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        mst_edges: List[Tuple[float, int, int]] = []
        for dist, i, j in edges:
            if find(i) != find(j):
                union(i, j)
                mst_edges.append((dist, i, j))

        if mst_edges:
            mst_dists = sorted([d for d, _, _ in mst_edges])
            median_mst = float(np.median(mst_dists))
            cut_threshold = median_mst * 1.5

            parent2 = list(range(n))

            def find2(x: int) -> int:
                while parent2[x] != x:
                    parent2[x] = parent2[parent2[x]]
                    x = parent2[x]
                return x

            def union2(a: int, b: int) -> None:
                ra, rb = find2(a), find2(b)
                if ra != rb:
                    parent2[ra] = rb

            for dist, i, j in mst_edges:
                if dist <= cut_threshold:
                    union2(i, j)

            clusters: dict = {}
            for i in range(n):
                root = find2(i)
                clusters.setdefault(root, []).append(i)
            cluster_groups = list(clusters.values())
        else:
            cluster_groups = [list(range(n))]
    elif distance_threshold is not None and n > 1:
        visited = [False] * n
        cluster_groups = []
        for i in range(n):
            if visited[i]:
                continue
            cluster = [i]
            visited[i] = True
            queue = [i]
            while queue:
                current = queue.pop(0)
                for j in range(n):
                    if visited[j]:
                        continue
                    dist = float(np.linalg.norm(centers[current] - centers[j]))
                    if dist <= distance_threshold:
                        visited[j] = True
                        cluster.append(j)
                        queue.append(j)
            cluster_groups.append(cluster)
    else:
        cluster_groups = [list(range(n))]

    grids: List[TagGrid] = []
    for grid_idx, indices in enumerate(cluster_groups):
        cluster_tags = [tags[idx] for idx in indices]
        all_corners = np.concatenate([t.corners for t in cluster_tags], axis=0)
        bbox = _bbox_from_points(all_corners, padding_ratio=0.1)
        center = _center_from_bbox(bbox)

        tag_centers = np.array([t.center for t in cluster_tags])
        unique_x = len(set(round(c[0], -1) for c in tag_centers))
        unique_y = len(set(round(c[1], -1) for c in tag_centers))
        cols = max(1, unique_x)
        rows = max(1, unique_y)

        grids.append(TagGrid(
            grid_id=f"G{grid_idx + 1}",
            tags=cluster_tags,
            bbox=bbox,
            center=center,
            rows=rows,
            cols=cols,
        ))

    return grids


def classify_checkerboards_by_size(
    boards: List[DetectedBoard],
) -> Tuple[List[DetectedBoard], List[DetectedBoard]]:
    checkerboards = [b for b in boards if b.board_type == "checkerboard"]
    if not checkerboards:
        return [], []

    areas = [b.area for b in checkerboards]
    median_area = float(np.median(areas))

    large: List[DetectedBoard] = []
    small: List[DetectedBoard] = []
    for board in checkerboards:
        if board.area >= median_area * 0.7:
            large.append(board)
        else:
            small.append(board)

    if not small:
        return large, []

    large.sort(key=lambda b: (b.center[0], b.center[1]))
    small.sort(key=lambda b: (b.center[0], b.center[1]))
    for idx, board in enumerate(large):
        board.board_id = f"B{idx + 1}"
    for idx, board in enumerate(small):
        board.board_id = f"S{idx + 1}"

    return large, small
