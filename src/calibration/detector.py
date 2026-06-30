"""DetectorMixin — board detection, ROI extraction, image preprocessing, template matching."""
import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

import cv2
import numpy as np

from src.calibration.calib_types import (
    BoardProfile,
    DetectionResult,
    EvalImageTransform,
)
from src.calibration.utils import (
    _is_custom_marker_board_type,
    _is_aruco_family_board_type,
    _is_apriltag_board_type,
    _is_circle_grid_board_type,
    _is_aruco_grid_board_type,
)


class DetectorMixin:

    _APRILTAG_FAMILY_CANDIDATES = (
        "tagStandard41h12",
        "tag36h11",
        "tag25h9",
        "tag16h5",
    )

    @staticmethod
    def _preprocess_template_match_image(
        gray_image: np.ndarray, board: BoardProfile, *, is_sim_sourced: bool = False
    ) -> np.ndarray:
        if is_sim_sourced:
            return gray_image
        if board.template_binary_threshold > 0:
            _, processed = cv2.threshold(
                gray_image,
                float(board.template_binary_threshold),
                255,
                cv2.THRESH_BINARY_INV,
            )
            return processed.astype(np.uint8)
        return gray_image

    @staticmethod
    def _points_bbox(points: np.ndarray) -> Tuple[int, int, int, int]:
        reshaped = points.reshape(-1, 2)
        min_x = int(math.floor(float(np.min(reshaped[:, 0]))))
        min_y = int(math.floor(float(np.min(reshaped[:, 1]))))
        max_x = int(math.ceil(float(np.max(reshaped[:, 0]))))
        max_y = int(math.ceil(float(np.max(reshaped[:, 1]))))
        return min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y)

    @staticmethod
    def _expand_bbox(
        bbox: Tuple[int, int, int, int],
        image_shape: Tuple[int, int],
        ratio: float = 0.18,
        min_pad: int = 24,
    ) -> Tuple[int, int, int, int]:
        x, y, width, height = bbox
        img_h, img_w = image_shape[:2]
        pad_x = max(min_pad, int(round(width * ratio)))
        pad_y = max(min_pad, int(round(height * ratio)))
        x0 = max(0, x - pad_x)
        y0 = max(0, y - pad_y)
        x1 = min(img_w, x + width + pad_x)
        y1 = min(img_h, y + height + pad_y)
        return x0, y0, max(1, x1 - x0), max(1, y1 - y0)

    @staticmethod
    def _bbox_iou(
        lhs: Tuple[int, int, int, int], rhs: Tuple[int, int, int, int]
    ) -> float:
        lx, ly, lw, lh = lhs
        rx, ry, rw, rh = rhs
        left = max(lx, rx)
        top = max(ly, ry)
        right = min(lx + lw, rx + rw)
        bottom = min(ly + lh, ry + rh)
        if left >= right or top >= bottom:
            return 0.0
        intersection = float((right - left) * (bottom - top))
        union = float(lw * lh + rw * rh) - intersection
        if union <= 0.0:
            return 0.0
        return intersection / union

    @staticmethod
    def _bbox_center(bbox: Tuple[int, int, int, int]) -> Tuple[float, float]:
        x, y, width, height = bbox
        return x + (width * 0.5), y + (height * 0.5)

    def _custom_board_content_geometry(self, board: BoardProfile) -> Tuple[float, float, float, float]:
        template_info = self.custom_templates.get(board.board_id, {})
        content_bbox = template_info.get("content_bbox")
        if content_bbox is not None:
            offset_x, offset_y, width, height = content_bbox
            return float(offset_x), float(offset_y), float(width), float(height)

        reference_bbox = board.template_source_roi or board.roi
        if reference_bbox is None:
            return 0.0, 0.0, 1.0, 1.0
        return 0.0, 0.0, float(reference_bbox[2]), float(reference_bbox[3])

    @staticmethod
    def _anchors_from_bbox(bbox: Tuple[int, int, int, int]) -> np.ndarray:
        x, y, width, height = bbox
        anchor_x = float(x)
        anchor_y = float(y)
        anchor_w = float(width)
        anchor_h = float(height)
        return np.array(
            [
                [anchor_x, anchor_y],
                [anchor_x + anchor_w - 1.0, anchor_y],
                [anchor_x + anchor_w - 1.0, anchor_y + anchor_h - 1.0],
                [anchor_x, anchor_y + anchor_h - 1.0],
                [anchor_x + anchor_w * 0.5, anchor_y + anchor_h * 0.5],
                [anchor_x + anchor_w * 0.25, anchor_y + anchor_h * 0.25],
                [anchor_x + anchor_w * 0.75, anchor_y + anchor_h * 0.25],
                [anchor_x + anchor_w * 0.75, anchor_y + anchor_h * 0.75],
                [anchor_x + anchor_w * 0.25, anchor_y + anchor_h * 0.75],
            ],
            dtype=np.float32,
        )

    @staticmethod
    def _checkerboard_outline(
        points: np.ndarray,
        board_size: Optional[Tuple[int, int]],
    ) -> Optional[np.ndarray]:
        if board_size is None:
            return None
        cols, rows = int(board_size[0]), int(board_size[1])
        reshaped = points.reshape(-1, 2).astype(np.float32)
        if reshaped.shape[0] != cols * rows or cols < 2 or rows < 2:
            return None
        grid = reshaped.reshape(rows, cols, 2)

        top_left = grid[0, 0]
        top_right = grid[0, -1]
        bottom_right = grid[-1, -1]
        bottom_left = grid[-1, 0]

        top_step = grid[0, 1] - grid[0, 0]
        right_step = grid[1, -1] - grid[0, -1]
        bottom_step = grid[-1, -1] - grid[-1, -2]
        left_step = grid[-1, 0] - grid[-2, 0]

        outline = np.array(
            [
                top_left - top_step - (grid[1, 0] - grid[0, 0]),
                top_right + (grid[0, -1] - grid[0, -2]) - right_step,
                bottom_right + bottom_step + (grid[-1, -1] - grid[-2, -1]),
                bottom_left - (grid[-1, 1] - grid[-1, 0]) + left_step,
            ],
            dtype=np.float32,
        )
        return outline

    def _reference_detection_from_board_geometry(
        self,
        board: BoardProfile,
    ) -> Optional[DetectionResult]:
        if not _is_custom_marker_board_type(board.board_type):
            return None
        reference_bbox = board.template_source_roi or board.roi
        if reference_bbox is None:
            return None
        offset_x, offset_y, width, height = self._custom_board_content_geometry(board)
        reference_bbox = (
            int(round(reference_bbox[0] + offset_x)),
            int(round(reference_bbox[1] + offset_y)),
            max(1, int(round(width))),
            max(1, int(round(height))),
        )
        anchors = self._anchors_from_bbox(reference_bbox)
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(anchors.shape[0]),
            ordered_points=anchors,
            board_type=board.board_type,
            roi_used=reference_bbox,
            detector="reference_roi",
        )

    def _extract_roi(
        self,
        image: np.ndarray,
        roi: Optional[Tuple[int, int, int, int]],
        padding: int = 0,
    ) -> Tuple[np.ndarray, Tuple[int, int]]:
        if roi is None:
            return image, (0, 0)

        x, y, width, height = roi
        if padding > 0:
            x -= padding
            y -= padding
            width += padding * 2
            height += padding * 2
        img_h, img_w = image.shape[:2]
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(img_w, x + width)
        y1 = min(img_h, y + height)
        if x0 >= x1 or y0 >= y1:
            raise ValueError("roi is outside image bounds")
        return image[y0:y1, x0:x1], (x0, y0)

    def _detection_within_reference_roi(
        self,
        points: np.ndarray,
        board: BoardProfile,
        padding: int,
    ) -> bool:
        """Reject detections that lock onto a false pattern far from the
        reference ROI (typical when ROI-padding expansion grows the search
        region to 200+ px and the sim render has a look-alike pattern
        elsewhere).

        Returns True (accept) if:
          - board has no ROI (whole-image detection, nothing to validate),
          - no padding was applied (ROI itself is the search region), or
          - detection center is within max(padding, 0.75 * ref_span) of
            the reference ROI center.
        """
        if board.roi is None or padding <= 0:
            return True
        if points.size == 0:
            return True
        pts = points.reshape(-1, 2)
        det_cx = float((pts[:, 0].min() + pts[:, 0].max()) / 2.0)
        det_cy = float((pts[:, 1].min() + pts[:, 1].max()) / 2.0)
        rx, ry, rw, rh = board.roi
        ref_cx = rx + rw / 2.0
        ref_cy = ry + rh / 2.0
        board_span = max(float(rw), float(rh))
        # Cap tolerance so large padding doesn't let detection drift into
        # neighboring boards. The board must be within 1.5× its own span.
        tolerance = max(0.75 * board_span, min(float(padding), 1.5 * board_span))
        return abs(det_cx - ref_cx) <= tolerance and abs(det_cy - ref_cy) <= tolerance

    def _detect_roi_padding_attempts(self, board: BoardProfile) -> List[int]:
        attempts = [0]
        configured_padding = max(0, int(board.detect_roi_padding))
        if configured_padding > 0:
            attempts.append(configured_padding)

        if board.roi is not None and configured_padding <= 0:
            _, _, width, height = board.roi
            base_span = max(1, int(max(width, height)))
            image_h, image_w = self.real_img.shape[:2]
            max_auto_padding = int(round(max(image_h, image_w) * 0.65))
            auto_paddings: List[int] = []

            if board.board_type == "checkerboard" or _is_aruco_family_board_type(board.board_type) or _is_apriltag_board_type(board.board_type) or _is_circle_grid_board_type(board.board_type) or _is_aruco_grid_board_type(board.board_type):
                auto_paddings.extend(
                    [
                        max(120, int(round(base_span * 1.5))),
                        max(220, int(round(base_span * 2.5))),
                    ]
                )
            elif _is_custom_marker_board_type(board.board_type):
                source_roi = board.template_source_roi or board.roi
                _, _, source_w, source_h = source_roi
                source_span = max(1, int(max(source_w, source_h)))
                auto_paddings.extend(
                    [
                        max(240, int(round(source_span * 1.5))),
                        max(480, int(round(source_span * 3.0))),
                    ]
                )

            for padding_value in auto_paddings:
                attempts.append(min(max_auto_padding, padding_value))

        ordered_attempts: List[int] = []
        seen: set[int] = set()
        for padding in attempts:
            normalized = max(0, int(padding))
            if normalized in seen:
                continue
            ordered_attempts.append(normalized)
            seen.add(normalized)
        return ordered_attempts

    def _template_match_threshold_attempts(self, board: BoardProfile) -> List[float]:
        thresholds = [float(board.template_match_threshold)]
        if _is_custom_marker_board_type(board.board_type):
            thresholds.extend(
                [
                    max(0.45, float(board.template_match_threshold) * 0.85),
                    max(0.35, float(board.template_match_threshold) * 0.70),
                    0.25,
                ]
            )
        elif board.board_type == "checkerboard" and board.template_image:
            thresholds.extend(
                [
                    max(0.40, float(board.template_match_threshold) * 0.80),
                    max(0.30, float(board.template_match_threshold) * 0.65),
                ]
            )

        ordered_thresholds: List[float] = []
        seen: set[float] = set()
        for threshold in thresholds:
            normalized = round(max(0.05, min(1.0, float(threshold))), 6)
            if normalized in seen:
                continue
            ordered_thresholds.append(normalized)
            seen.add(normalized)
        return ordered_thresholds

    @staticmethod
    def _template_match_expected_bbox(board: BoardProfile) -> Optional[Tuple[int, int, int, int]]:
        if board.board_type == "custom_maker" and board.template_source_roi is not None:
            return board.template_source_roi
        return board.roi

    def _template_match_candidate_geometry(
        self,
        board: BoardProfile,
        template_shape: Tuple[int, int],
        match_crop: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[float, float, float, float]:
        template_h, template_w = template_shape[:2]
        anchor_shift_x = 0.0
        anchor_shift_y = 0.0
        candidate_w = float(template_w)
        candidate_h = float(template_h)
        if board.board_type == "custom_maker":
            content_x, content_y, candidate_w, candidate_h = self._custom_board_content_geometry(board)
            effective_match_crop = match_crop
            if effective_match_crop is None:
                template_info = self.custom_templates.get(board.board_id, {})
                effective_match_crop = template_info.get("match_crop")
            if effective_match_crop is not None:
                crop_x, crop_y, _, _ = effective_match_crop
                anchor_shift_x = content_x - float(crop_x)
                anchor_shift_y = content_y - float(crop_y)
            else:
                anchor_shift_x = content_x
                anchor_shift_y = content_y
        return anchor_shift_x, anchor_shift_y, candidate_w, candidate_h

    def _template_match_best_local_candidate(
        self,
        board: BoardProfile,
        response: np.ndarray,
        offset: Tuple[int, int],
        template_shape: Tuple[int, int],
        image_shape: Tuple[int, int],
        match_crop: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Tuple[float, Tuple[int, int]]]:
        expected_bbox = self._template_match_expected_bbox(board)
        if expected_bbox is None:
            _, max_value, _, max_location = cv2.minMaxLoc(response)
            return float(max_value), (int(max_location[0]), int(max_location[1]))

        expand_ratio = 0.12 if board.board_type == "checkerboard" else 0.06
        expand_min_pad = 20 if board.board_type == "checkerboard" else 12
        allowed_bbox = self._expand_bbox(
            expected_bbox,
            image_shape[:2],
            ratio=expand_ratio,
            min_pad=expand_min_pad,
        )
        allow_x, allow_y, allow_w, allow_h = allowed_bbox
        shift_x, shift_y, cand_w, cand_h = self._template_match_candidate_geometry(
            board,
            template_shape,
            match_crop,
        )

        response_h, response_w = response.shape[:2]
        x_coords = offset[0] + np.arange(response_w, dtype=np.float32)
        y_coords = offset[1] + np.arange(response_h, dtype=np.float32)
        grid_x, grid_y = np.meshgrid(x_coords, y_coords)
        center_x = grid_x + shift_x + (cand_w * 0.5)
        center_y = grid_y + shift_y + (cand_h * 0.5)
        valid_mask = (
            (center_x >= allow_x)
            & (center_x <= (allow_x + allow_w))
            & (center_y >= allow_y)
            & (center_y <= (allow_y + allow_h))
        )
        if not bool(np.any(valid_mask)):
            return None

        masked_response = np.where(valid_mask, response, -np.inf)
        flat_index = int(np.argmax(masked_response))
        max_value = float(masked_response.flat[flat_index])
        if not np.isfinite(max_value):
            return None
        max_y, max_x = np.unravel_index(flat_index, masked_response.shape)

        expected_center_x = allow_x + (allow_w * 0.5)
        expected_center_y = allow_y + (allow_h * 0.5)
        score_band_tolerance = 0.03 if _is_custom_marker_board_type(board.board_type) else 0.01
        near_best_mask = valid_mask & (response >= (max_value - score_band_tolerance))
        if bool(np.any(near_best_mask)):
            center_distance = np.square(center_x - expected_center_x) + np.square(
                center_y - expected_center_y
            )
            center_distance = np.where(near_best_mask, center_distance, np.inf)
            nearest_index = int(np.argmin(center_distance))
            nearest_y, nearest_x = np.unravel_index(nearest_index, center_distance.shape)
            nearest_value = float(response[nearest_y, nearest_x])
            if np.isfinite(nearest_value):
                return nearest_value, (int(nearest_x), int(nearest_y))

        return max_value, (int(max_x), int(max_y))

    def _custom_board_image_penalty(
        self,
        board: BoardProfile,
        real_detection: DetectionResult,
        sim_detection: DetectionResult,
        sim_eval_image: Optional[np.ndarray],
    ) -> float:
        if sim_eval_image is None:
            return 0.0

        real_bbox = self._points_bbox(real_detection.ordered_points)
        sim_bbox = self._points_bbox(sim_detection.ordered_points)
        rx, ry, rw, rh = real_bbox
        sx, sy, sw, sh = sim_bbox
        real_patch = self.real_img[ry : ry + rh, rx : rx + rw]
        sim_patch = sim_eval_image[sy : sy + sh, sx : sx + sw]
        if real_patch.size == 0 or sim_patch.size == 0:
            return 0.0

        target_w = max(8, min(real_patch.shape[1], sim_patch.shape[1]))
        target_h = max(8, min(real_patch.shape[0], sim_patch.shape[0]))
        real_patch = cv2.resize(real_patch, (target_w, target_h), interpolation=cv2.INTER_AREA)
        sim_patch = cv2.resize(sim_patch, (target_w, target_h), interpolation=cv2.INTER_AREA)
        real_processed = self._preprocess_template_match_image(real_patch, board)
        sim_processed = self._preprocess_template_match_image(sim_patch, board)
        mae = float(
            np.mean(np.abs(real_processed.astype(np.float32) - sim_processed.astype(np.float32)))
        ) / 255.0
        return mae * 100.0

    def _custom_board_geometric_penalty(
        self,
        board: BoardProfile,
        real_detection: DetectionResult,
        sim_detection: DetectionResult,
        sim_eval_image: Optional[np.ndarray],
    ) -> float:
        """Pure geometric structure penalty — no pixel comparison.

        Only uses homography consistency checks:
        1. RANSAC outlier fraction — structural mismatch
        2. SVD condition number — perspective distortion

        Suitable for all board types. Returns additive penalty in pixel units.
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

    def _prepare_eval_image(self, image: np.ndarray) -> np.ndarray:
        source_h, source_w = image.shape[:2]
        target_h, target_w = self.real_img.shape[:2]
        if source_h <= 0 or source_w <= 0:
            raise ValueError("image has invalid shape")
        if source_w * target_h != target_w * source_h:
            print(
                "WARNING: Captured image aspect ratio does not match real_image: "
                f"captured={source_w}x{source_h}, real={target_w}x{target_h} - will resize"
            )
        if image.shape != self.real_img.shape:
            target_w = int(self.real_img.shape[1])
            target_h = int(self.real_img.shape[0])
            if self.keep_aspect_resize:
                h, w = image.shape[:2]
                scale = min(target_w / max(1, w), target_h / max(1, h))
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
                if resized.ndim == 2:
                    canvas = np.zeros((target_h, target_w), dtype=resized.dtype)
                else:
                    canvas = np.zeros(
                        (target_h, target_w, resized.shape[2]), dtype=resized.dtype
                    )
                off_x = (target_w - new_w) // 2
                off_y = (target_h - new_h) // 2
                canvas[off_y : off_y + new_h, off_x : off_x + new_w] = resized
                image = canvas
            else:
                image = cv2.resize(image, (target_w, target_h))
        return image

    def _get_eval_transform(self, image_shape: Tuple[int, int]) -> EvalImageTransform:
        source_h, source_w = image_shape[:2]
        target_h = int(self.real_img.shape[0])
        target_w = int(self.real_img.shape[1])

        if source_h == target_h and source_w == target_w:
            return EvalImageTransform(
                scale_x=1.0,
                scale_y=1.0,
                offset_x=0,
                offset_y=0,
                eval_width=target_w,
                eval_height=target_h,
                source_width=source_w,
                source_height=source_h,
            )

        if self.keep_aspect_resize:
            scale = min(target_w / max(1, source_w), target_h / max(1, source_h))
            eval_w = max(1, int(round(source_w * scale)))
            eval_h = max(1, int(round(source_h * scale)))
            return EvalImageTransform(
                scale_x=scale,
                scale_y=scale,
                offset_x=(target_w - eval_w) // 2,
                offset_y=(target_h - eval_h) // 2,
                eval_width=eval_w,
                eval_height=eval_h,
                source_width=source_w,
                source_height=source_h,
            )

        return EvalImageTransform(
            scale_x=target_w / max(1, source_w),
            scale_y=target_h / max(1, source_h),
            offset_x=0,
            offset_y=0,
            eval_width=target_w,
            eval_height=target_h,
            source_width=source_w,
            source_height=source_h,
        )

    def _map_eval_points_to_source(
        self, points: np.ndarray, transform: EvalImageTransform
    ) -> np.ndarray:
        reshaped = points.reshape(-1, 2).astype(np.float32)
        mapped = np.empty_like(reshaped)
        mapped[:, 0] = (reshaped[:, 0] - float(transform.offset_x)) / float(transform.scale_x)
        mapped[:, 1] = (reshaped[:, 1] - float(transform.offset_y)) / float(transform.scale_y)
        mapped[:, 0] = np.clip(mapped[:, 0], 0, max(0, transform.source_width - 1))
        mapped[:, 1] = np.clip(mapped[:, 1], 0, max(0, transform.source_height - 1))
        return mapped.reshape(points.shape)

    @staticmethod
    def _preprocess_variants(gray: np.ndarray) -> List[np.ndarray]:
        variants = [gray]
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        variants.append(clahe.apply(gray))
        variants.append(cv2.GaussianBlur(gray, (3, 3), 0))
        return variants

    @staticmethod
    def _resolve_aruco_dictionary(dictionary_name: str):
        if not hasattr(cv2, "aruco"):
            raise RuntimeError("OpenCV aruco module is unavailable")

        normalized_name = str(dictionary_name or "DICT_4X4_50").strip().upper()
        if normalized_name.startswith("CV2.ARUCO."):
            normalized_name = normalized_name.split(".")[-1]
        dictionary_id = getattr(cv2.aruco, normalized_name, None)
        if dictionary_id is None:
            raise ValueError(f"Unsupported ArUco dictionary: {dictionary_name}")
        return cv2.aruco.getPredefinedDictionary(dictionary_id)

    @staticmethod
    def _flatten_aruco_marker_points(
        marker_corners: Sequence[np.ndarray],
        marker_ids: Optional[np.ndarray],
        offset: Tuple[int, int],
    ) -> np.ndarray:
        if marker_ids is None or not marker_corners:
            return np.empty((0, 2), dtype=np.float32)

        flat_ids = np.asarray(marker_ids, dtype=np.int32).reshape(-1)
        ordered_markers: List[Tuple[int, np.ndarray]] = []
        for index, marker_id in enumerate(flat_ids):
            if index >= len(marker_corners):
                break
            corners = np.asarray(marker_corners[index], dtype=np.float32).reshape(-1, 2)
            if corners.shape[0] < 4:
                continue
            ordered_markers.append((int(marker_id), corners))

        if not ordered_markers:
            return np.empty((0, 2), dtype=np.float32)

        ordered_markers.sort(key=lambda item: item[0])
        ordered_points = np.concatenate([item[1] for item in ordered_markers], axis=0)
        ordered_points[:, 0] += float(offset[0])
        ordered_points[:, 1] += float(offset[1])
        return ordered_points.astype(np.float32)

    def _detect_checkerboard(
        self, gray_image: np.ndarray, board: BoardProfile
    ) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)

        found = False
        corners = None
        offset = (0, 0)
        for padding in roi_attempts:
            roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
            for candidate in self._preprocess_variants(roi_img):
                flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
                found, corners = cv2.findChessboardCornersSB(candidate, board.board_size, flags=flags)
                if found and corners is not None:
                    break

                fallback_flags = (
                    cv2.CALIB_CB_ADAPTIVE_THRESH
                    | cv2.CALIB_CB_NORMALIZE_IMAGE
                    | cv2.CALIB_CB_FAST_CHECK
                )
                found, corners = cv2.findChessboardCorners(candidate, board.board_size, fallback_flags)
                if found and corners is not None:
                    criteria = (
                        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER,
                        30,
                        0.001,
                    )
                    corners = cv2.cornerSubPix(candidate, corners, (11, 11), (-1, -1), criteria)
                    break
            if found and corners is not None:
                eval_corners = corners.reshape(-1, 2).astype(np.float32)
                eval_corners[:, 0] += float(offset[0])
                eval_corners[:, 1] += float(offset[1])
                if not self._detection_within_reference_roi(eval_corners, board, padding):
                    found = False
                    corners = None
                    continue
                break

        if not found or corners is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="checkerboard",
                error_message="checkerboard not detected",
            )

        ordered_points = corners.reshape(-1, 2).astype(np.float32)
        ordered_points[:, 0] += float(offset[0])
        ordered_points[:, 1] += float(offset[1])
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(len(ordered_points)),
            ordered_points=ordered_points,
            board_type=board.board_type,
            roi_used=board.roi,
            detector="checkerboard",
        )

    def _detect_aruco(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)
        dictionary = self._resolve_aruco_dictionary(board.aruco_dictionary)
        detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())

        for padding in roi_attempts:
            roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
            marker_corners, marker_ids, _ = detector.detectMarkers(roi_img)
            ordered_points = self._flatten_aruco_marker_points(marker_corners, marker_ids, offset)
            if ordered_points.shape[0] == 0:
                continue
            if not self._detection_within_reference_roi(ordered_points, board, padding):
                continue
            return DetectionResult(
                board_id=board.board_id,
                success=True,
                point_count=int(ordered_points.shape[0]),
                ordered_points=ordered_points,
                board_type=board.board_type,
                roi_used=board.roi,
                detector="aruco",
            )

        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="aruco",
            error_message="aruco markers not detected",
        )

    @staticmethod
    def _flatten_apriltag_detections(
        detections: list,
        offset: Tuple[int, int],
    ) -> np.ndarray:
        if not detections:
            return np.empty((0, 2), dtype=np.float32)
        ordered = sorted(detections, key=lambda d: d.tag_id)
        points: List[np.ndarray] = []
        for det in ordered:
            corners = np.asarray(det.corners, dtype=np.float32).reshape(-1, 2)
            if corners.shape[0] < 4:
                continue
            points.append(corners)
        if not points:
            return np.empty((0, 2), dtype=np.float32)
        all_points = np.concatenate(points, axis=0)
        all_points[:, 0] += float(offset[0])
        all_points[:, 1] += float(offset[1])
        return all_points.astype(np.float32)

    def _create_apriltag_detector(self, tag_family: str) -> object:
        try:
            from pupil_apriltags import Detector
        except ImportError:
            raise RuntimeError(
                "pupil_apriltags is not installed. "
                "Install it with: pip install pupil_apriltags"
            )
        return Detector(families=tag_family)

    def _detect_apriltag(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)

        tag_family = str(board.tag_family or "").strip()
        auto_detect_family = not tag_family or tag_family.lower() == "auto"
        family_candidates = (
            list(self._APRILTAG_FAMILY_CANDIDATES) if auto_detect_family else [tag_family]
        )

        for family in family_candidates:
            try:
                detector = self._create_apriltag_detector(family)
            except Exception:
                if auto_detect_family:
                    continue
                raise

            for padding in roi_attempts:
                roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
                detections = detector.detect(roi_img)
                ordered_points = self._flatten_apriltag_detections(detections, offset)
                if ordered_points.shape[0] == 0:
                    continue
                if not self._detection_within_reference_roi(ordered_points, board, padding):
                    continue
                return DetectionResult(
                    board_id=board.board_id,
                    success=True,
                    point_count=int(ordered_points.shape[0]),
                    ordered_points=ordered_points,
                    board_type=board.board_type,
                    roi_used=board.roi,
                    detector="apriltag",
                )

        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="apriltag",
            error_message="apriltag markers not detected",
        )

    def _detect_circle_grid(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        if board.board_size is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="circle_grid",
                error_message="circle_grid requires board_size",
            )

        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)
        cols, rows = board.board_size
        is_asymmetric = board.grid_type.strip().lower() == "asymmetric"
        flags = cv2.CALIB_CB_ASYMMETRIC_GRID if is_asymmetric else cv2.CALIB_CB_SYMMETRIC_GRID

        for padding in roi_attempts:
            roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
            found, centers = cv2.findCirclesGrid(
                roi_img, (cols, rows), None, flags,
            )
            if not found or centers is None:
                continue
            pts = centers.reshape(-1, 2).astype(np.float32)
            pts[:, 0] += float(offset[0])
            pts[:, 1] += float(offset[1])
            if not self._detection_within_reference_roi(pts, board, padding):
                continue
            return DetectionResult(
                board_id=board.board_id,
                success=True,
                point_count=int(len(pts)),
                ordered_points=pts,
                board_type=board.board_type,
                roi_used=board.roi,
                detector="circle_grid",
            )

        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="circle_grid",
            error_message="circle grid not detected",
        )

    def _detect_aruco_grid(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        if not hasattr(cv2, "aruco"):
            raise RuntimeError("OpenCV aruco module is unavailable")
        if board.board_size is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="aruco_grid",
                error_message="aruco_grid requires board_size (cols, rows of markers)",
            )

        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)
        dictionary = self._resolve_aruco_dictionary(board.aruco_dictionary)
        cols, rows = board.board_size
        marker_length = max(float(board.square_size), 1e-6)
        marker_separation = max(float(board.marker_separation), 1e-6)
        grid_board = cv2.aruco.GridBoard(
            (cols, rows), marker_length, marker_separation, dictionary,
        )

        for padding in roi_attempts:
            roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
            detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
            marker_corners, marker_ids, _ = detector.detectMarkers(roi_img)
            if marker_ids is None or not marker_corners:
                continue

            obj_pts, img_pts = grid_board.matchImagePoints(marker_corners, marker_ids)
            if obj_pts is None or len(obj_pts) == 0:
                continue

            pts = np.asarray(img_pts, dtype=np.float32).reshape(-1, 2)
            pts[:, 0] += float(offset[0])
            pts[:, 1] += float(offset[1])
            if not self._detection_within_reference_roi(pts, board, padding):
                continue
            return DetectionResult(
                board_id=board.board_id,
                success=True,
                point_count=int(len(pts)),
                ordered_points=pts,
                board_type=board.board_type,
                roi_used=board.roi,
                detector="aruco_grid",
            )

        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="aruco_grid",
            error_message="aruco grid not detected",
        )

    def _detect_charuco(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        if board.board_size is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="charuco",
                error_message="missing charuco board_size",
            )

        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)
        dictionary = self._resolve_aruco_dictionary(board.aruco_dictionary)
        marker_length_ratio = min(max(float(board.marker_length_ratio), 1e-3), 0.999)
        square_length = max(float(board.square_size), 1e-6)
        marker_length = square_length * marker_length_ratio
        charuco_board = cv2.aruco.CharucoBoard(
            board.board_size,
            square_length,
            marker_length,
            dictionary,
        )
        detector = cv2.aruco.CharucoDetector(charuco_board)
        best_error_message = "charuco corners not detected"

        for padding in roi_attempts:
            roi_img, offset = self._extract_roi(eval_image, board.roi, padding=padding)
            charuco_corners, charuco_ids, _, marker_ids = detector.detectBoard(roi_img)
            if charuco_corners is None or charuco_ids is None:
                if marker_ids is not None and len(np.asarray(marker_ids).reshape(-1)) > 0:
                    best_error_message = "charuco markers found but corners not interpolated"
                continue

            ordered_points = np.asarray(charuco_corners, dtype=np.float32).reshape(-1, 2)
            flat_ids = np.asarray(charuco_ids, dtype=np.int32).reshape(-1)
            if ordered_points.shape[0] == 0 or flat_ids.shape[0] == 0:
                continue
            ordered_points = ordered_points[np.argsort(flat_ids)]
            ordered_points[:, 0] += float(offset[0])
            ordered_points[:, 1] += float(offset[1])
            if not self._detection_within_reference_roi(ordered_points, board, padding):
                continue
            return DetectionResult(
                board_id=board.board_id,
                success=True,
                point_count=int(ordered_points.shape[0]),
                ordered_points=ordered_points.astype(np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="charuco",
            )

        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="charuco",
            error_message=best_error_message,
        )

    def _detect_template_board(
        self,
        gray_image: np.ndarray,
        board: BoardProfile,
        search_mask: Optional[np.ndarray] = None,
    ) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_img, offset = self._extract_roi(eval_image, board.roi)
        roi_mask = None
        if search_mask is not None:
            prepared_mask = self._prepare_eval_image(search_mask)
            roi_mask, _ = self._extract_roi(prepared_mask, board.roi)
            roi_mask = roi_mask.astype(np.uint8)
            if int(cv2.countNonZero(roi_mask)) < max(8, board.min_match_count):
                return DetectionResult(
                    board_id=board.board_id,
                    success=False,
                    point_count=0,
                    ordered_points=np.empty((0, 2), dtype=np.float32),
                    board_type=board.board_type,
                    roi_used=board.roi,
                    detector="template",
                    error_message="search mask exhausted",
                )

        template_info = self.custom_templates.get(board.board_id)
        if template_info is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="missing template info",
            )

        kp_scene, des_scene = self.orb.detectAndCompute(roi_img, roi_mask)
        if des_scene is None or kp_scene is None or len(kp_scene) < board.min_match_count:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="insufficient custom board features",
            )

        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        raw_matches = bf.knnMatch(template_info["des"], des_scene, k=2)
        good_matches = []
        for pair in raw_matches:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < 0.75 * n.distance:
                good_matches.append(m)

        if len(good_matches) < max(8, board.min_match_count):
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="insufficient homography matches",
            )

        src_pts = np.float32([template_info["kp"][m.queryIdx].pt for m in good_matches]).reshape(
            -1, 1, 2
        )
        dst_pts = np.float32([kp_scene[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        homography, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 4.0)
        if homography is None or mask is None or int(mask.sum()) < 8:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="custom board homography failed",
            )

        projected = cv2.perspectiveTransform(
            template_info["anchors"].reshape(-1, 1, 2), homography
        ).reshape(-1, 2)
        projected[:, 0] += float(offset[0])
        projected[:, 1] += float(offset[1])
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(projected.shape[0]),
            ordered_points=projected.astype(np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="template",
        )

    def _detect_template_match_board(
        self, gray_image: np.ndarray, board: BoardProfile
    ) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_attempts = self._detect_roi_padding_attempts(board)
        threshold_attempts = self._template_match_threshold_attempts(board)

        template_info = self.custom_templates.get(board.board_id)
        if template_info is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template_match",
                error_message="missing template info",
            )

        template_gray = template_info["template"]
        template_variants: List[Tuple[np.ndarray, Tuple[int, int, int, int]]] = []
        match_template_gray = template_info.get("match_template")
        match_crop = template_info.get("match_crop")
        if match_template_gray is not None and match_crop is not None:
            template_variants.append((match_template_gray, match_crop))
        template_variants.append(
            (template_gray, (0, 0, int(template_gray.shape[1]), int(template_gray.shape[0])))
        )
        best_failure_message = "search roi smaller than template"
        best_failure_value: Optional[float] = None
        match_x: Optional[float] = None
        match_y: Optional[float] = None
        matched_crop: Tuple[int, int, int, int] = (0, 0, int(template_gray.shape[1]), int(template_gray.shape[0]))
        matched_template_shape: Tuple[int, int] = (int(template_gray.shape[0]), int(template_gray.shape[1]))
        offset = (0, 0)

        sim_sourced = board.board_id in self._sim_sourced_board_ids

        for padding in roi_attempts:
            roi_img, current_offset = self._extract_roi(eval_image, board.roi, padding=padding)
            search_image = self._preprocess_template_match_image(roi_img, board, is_sim_sourced=sim_sourced)
            for variant_gray, variant_crop in template_variants:
                if (
                    roi_img.shape[0] < variant_gray.shape[0]
                    or roi_img.shape[1] < variant_gray.shape[1]
                ):
                    continue

                template_image = self._preprocess_template_match_image(variant_gray, board, is_sim_sourced=sim_sourced)
                response = cv2.matchTemplate(search_image, template_image, cv2.TM_CCOEFF_NORMED)
                best_candidate = self._template_match_best_local_candidate(
                    board,
                    response,
                    current_offset,
                    variant_gray.shape,
                    eval_image.shape,
                    variant_crop,
                )
                if best_candidate is None:
                    continue
                current_max_value, current_max_location = best_candidate
                for threshold in threshold_attempts:
                    if best_failure_value is None or current_max_value > best_failure_value:
                        best_failure_value = float(current_max_value)
                        best_failure_message = (
                            f"template match below threshold: {current_max_value:.3f} < "
                            f"{threshold:.3f}"
                        )
                    if current_max_value < threshold:
                        continue

                    offset = current_offset
                    match_x = float(offset[0] + current_max_location[0])
                    match_y = float(offset[1] + current_max_location[1])
                    matched_crop = variant_crop
                    matched_template_shape = (int(variant_gray.shape[0]), int(variant_gray.shape[1]))
                    break
                if match_x is not None and match_y is not None:
                    break
            if match_x is not None and match_y is not None:
                break

        if match_x is None or match_y is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template_match",
                error_message=best_failure_message,
                match_score=best_failure_value,
            )

        template_h, template_w = matched_template_shape
        anchor_x = match_x
        anchor_y = match_y
        anchor_w = float(template_w)
        anchor_h = float(template_h)
        if board.board_type == "custom_maker":
            content_x, content_y, content_w, content_h = self._custom_board_content_geometry(board)
            crop_x, crop_y, _, _ = matched_crop
            anchor_x = match_x - float(crop_x) + content_x
            anchor_y = match_y - float(crop_y) + content_y
            anchor_w = float(content_w)
            anchor_h = float(content_h)
        anchors = np.array(
            [
                [anchor_x, anchor_y],
                [anchor_x + anchor_w - 1.0, anchor_y],
                [anchor_x + anchor_w - 1.0, anchor_y + anchor_h - 1.0],
                [anchor_x, anchor_y + anchor_h - 1.0],
                [anchor_x + anchor_w * 0.5, anchor_y + anchor_h * 0.5],
                [anchor_x + anchor_w * 0.25, anchor_y + anchor_h * 0.25],
                [anchor_x + anchor_w * 0.75, anchor_y + anchor_h * 0.25],
                [anchor_x + anchor_w * 0.75, anchor_y + anchor_h * 0.75],
                [anchor_x + anchor_w * 0.25, anchor_y + anchor_h * 0.75],
            ],
            dtype=np.float32,
        )
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(anchors.shape[0]),
            ordered_points=anchors,
            board_type=board.board_type,
            roi_used=board.roi,
            detector="template_match",
        )

    def _detect_custom_groundmaker(
        self, gray_image: np.ndarray, board: BoardProfile
    ) -> DetectionResult:
        if board.custom_detector == "template_match":
            return self._detect_template_match_board(gray_image, board)
        return self._detect_template_board(gray_image, board)

    def _detect_board(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        if board.board_type == "checkerboard":
            primary = self._detect_checkerboard(gray_image, board)
            if board.template_image:
                fallback_detectors = [self._detect_template_match_board]
                if primary.success and board.roi is not None:
                    primary_bbox = self._points_bbox(primary.ordered_points)
                    expected_bbox = self._expand_bbox(
                        board.roi,
                        self.real_img.shape[:2],
                        ratio=0.20,
                        min_pad=24,
                    )
                    primary_center_x, primary_center_y = self._bbox_center(primary_bbox)
                    exp_x, exp_y, exp_w, exp_h = expected_bbox
                    primary_matches_roi = (
                        self._bbox_iou(primary_bbox, expected_bbox) > 0.0
                        or (
                            exp_x <= primary_center_x <= (exp_x + exp_w)
                            and exp_y <= primary_center_y <= (exp_y + exp_h)
                        )
                    )
                    if primary_matches_roi:
                        return primary
                elif primary.success:
                    return primary
                for detector in fallback_detectors:
                    fallback = detector(gray_image, board)
                    if fallback.success:
                        return fallback
            if primary.success:
                return primary
            return primary
        if board.board_type == "aruco":
            return self._detect_aruco(gray_image, board)
        if board.board_type == "charuco":
            return self._detect_charuco(gray_image, board)
        if _is_apriltag_board_type(board.board_type):
            return self._detect_apriltag(gray_image, board)
        if _is_circle_grid_board_type(board.board_type):
            return self._detect_circle_grid(gray_image, board)
        if _is_aruco_grid_board_type(board.board_type):
            return self._detect_aruco_grid(gray_image, board)
        if _is_custom_marker_board_type(board.board_type):
            return self._detect_custom_groundmaker(gray_image, board)
        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="unknown",
            error_message=f"unsupported board_type: {board.board_type}",
        )

    def _detect_template_instances(
        self, gray_image: np.ndarray, board: BoardProfile, max_instances: int
    ) -> List[Tuple[DetectionResult, Tuple[int, int, int, int]]]:
        prepared = self._prepare_eval_image(gray_image)
        search_mask = np.full(prepared.shape[:2], 255, dtype=np.uint8)
        detections: List[Tuple[DetectionResult, Tuple[int, int, int, int]]] = []

        for _ in range(max_instances):
            detection = self._detect_template_board(prepared, board, search_mask=search_mask)
            if not detection.success or detection.ordered_points.size == 0:
                break

            bbox = self._expand_bbox(
                self._points_bbox(detection.ordered_points), prepared.shape[:2]
            )
            is_duplicate = any(self._bbox_iou(bbox, existing_bbox) >= 0.6 for _, existing_bbox in detections)
            x, y, width, height = bbox
            search_mask[y : y + height, x : x + width] = 0
            if is_duplicate:
                continue
            detections.append((detection, bbox))

        return detections

    def _detect_checkerboard_instances(
        self, gray_image: np.ndarray, board: BoardProfile, max_instances: int
    ) -> List[Tuple[DetectionResult, Tuple[int, int, int, int]]]:
        prepared = self._prepare_eval_image(gray_image)
        working = prepared.copy()
        detections: List[Tuple[DetectionResult, Tuple[int, int, int, int]]] = []

        for _ in range(max_instances):
            detection = self._detect_checkerboard(working, board)
            if not detection.success or detection.ordered_points.size == 0:
                break

            geometry_ratio, mean_step = self._checkerboard_geometry_ratio(detection, board)
            if geometry_ratio > 6.0:
                suppress_radius = max(8, int(round(mean_step * 0.9)))
                for point in detection.ordered_points.reshape(-1, 2):
                    cv2.circle(
                        working,
                        (int(round(float(point[0]))), int(round(float(point[1])))),
                        suppress_radius,
                        0,
                        -1,
                    )
                continue

            bbox = self._expand_bbox(
                self._points_bbox(detection.ordered_points), prepared.shape[:2]
            )
            is_duplicate = any(self._bbox_iou(bbox, existing_bbox) >= 0.6 for _, existing_bbox in detections)
            x, y, width, height = bbox
            working[y : y + height, x : x + width] = 0
            if is_duplicate:
                continue
            detections.append((detection, bbox))

        return detections

    @staticmethod
    def _checkerboard_geometry_ratio(
        detection: DetectionResult, board: BoardProfile
    ) -> Tuple[float, float]:
        if board.board_size is None or detection.ordered_points.size == 0:
            return float("inf"), 0.0
        cols, rows = board.board_size
        try:
            points = detection.ordered_points.reshape(rows, cols, 2)
        except ValueError:
            return float("inf"), 0.0

        dx = np.linalg.norm(points[:, 1:, :] - points[:, :-1, :], axis=2)
        dy = np.linalg.norm(points[1:, :, :] - points[:-1, :, :], axis=2)
        steps = np.concatenate([dx.ravel(), dy.ravel()])
        if steps.size == 0:
            return float("inf"), 0.0
        min_step = float(np.min(steps))
        max_step = float(np.max(steps))
        mean_step = float(np.mean(steps))
        if min_step <= 1e-6:
            return float("inf"), mean_step
        return max_step / min_step, mean_step

    def _detect_reference_boards(self) -> Dict[str, DetectionResult]:
        detections: Dict[str, DetectionResult] = {}
        visible_count = 0
        for board in self.boards:
            detection = self._reference_detection_from_board_geometry(board)
            if detection is None:
                detection = self._detect_board(self.real_img, board)
            if self._is_visible(detection, self._effective_detection_min_points(board, detection)):
                visible_count += 1
            detections[board.board_id] = detection
        if visible_count == 0:
            raise RuntimeError(
                "No boards are visible in reference image. "
                "Cannot optimize without comparable targets."
            )
        return detections

    @staticmethod
    def _align_checkerboard_ordering(
        sim_points: np.ndarray,
        real_points: np.ndarray,
        board_size: Tuple[int, int],
    ) -> np.ndarray:
        cols, rows = board_size
        n = cols * rows
        if len(sim_points) < n or len(real_points) < n:
            return sim_points

        sim_grid = sim_points[:n].reshape(rows, cols, 2)
        r0 = real_points[0]

        candidates = [
            sim_points[:n],
            sim_points[:n][::-1],
            sim_grid[::-1].reshape(-1, 2),
            sim_grid[:, ::-1].reshape(-1, 2),
        ]

        best = sim_points
        best_err = float("inf")
        for c in candidates:
            err = float(np.sum((c - real_points[:n]) ** 2))
            if err < best_err:
                best_err = err
                result = np.copy(sim_points)
                result[:n] = c
                best = result

        return best
