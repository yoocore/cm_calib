from __future__ import annotations
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import cv2
import numpy as np
from src.calibration.calib_types import TotalScoreDetail, BoardScoreDetail
from src.calibration.utils import _build_annotation_legend_lines


class AnnotationMixin:
    @staticmethod
    def _get_annotation_palette() -> List[Tuple[int, int, int]]:
        return [
            (70, 80, 230),
            (60, 170, 90),
            (220, 110, 60),
            (180, 60, 180),
            (70, 170, 200),
            (200, 200, 70),
            (80, 220, 120),
            (255, 180, 80),
            (80, 180, 255),
            (220, 120, 255),
            (255, 120, 120),
            (120, 255, 255),
        ]

    @staticmethod
    def _draw_annotated_label(
        image: np.ndarray,
        text: str,
        anchor: Tuple[int, int],
        color: Tuple[int, int, int],
        scale: float = 0.62,
        thickness: int = 2,
    ) -> None:
        font = cv2.FONT_HERSHEY_SIMPLEX
        (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
        x = max(8, int(anchor[0]))
        y = max(text_h + 12, int(anchor[1]))
        top_left = (x - 4, y - text_h - 8)
        bottom_right = (x + text_w + 6, y + baseline - 2)
        cv2.rectangle(image, top_left, bottom_right, color, -1)
        cv2.putText(
            image,
            text,
            (x, y - 4),
            font,
            scale,
            (16, 16, 16),
            thickness,
            cv2.LINE_AA,
        )

    @classmethod
    def _resolve_annotated_label_anchor(
        cls,
        image_shape: Tuple[int, ...],
        text: str,
        preferred_anchor: Tuple[int, int],
        occupied_boxes: List[Tuple[Tuple[int, int], Tuple[int, int]]],
        scale: float = 0.62,
        thickness: int = 2,
    ) -> Tuple[Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]]:
        offsets = [0, 24, -24, 48, -48, 72, -72, 96, -96]
        for delta_y in offsets:
            top_left, bottom_right, resolved_anchor = cls._annotated_label_bounds(
                image_shape,
                text,
                (preferred_anchor[0], preferred_anchor[1] + delta_y),
                scale=scale,
                thickness=thickness,
            )
            overlaps = any(
                not (
                    bottom_right[0] < other_top_left[0]
                    or other_bottom_right[0] < top_left[0]
                    or bottom_right[1] < other_top_left[1]
                    or other_bottom_right[1] < top_left[1]
                )
                for other_top_left, other_bottom_right in occupied_boxes
            )
            if not overlaps:
                return resolved_anchor, (top_left, bottom_right)

        top_left, bottom_right, resolved_anchor = cls._annotated_label_bounds(
            image_shape,
            text,
            preferred_anchor,
            scale=scale,
            thickness=thickness,
        )
        return resolved_anchor, (top_left, bottom_right)

    def annotate_existing_image(
        self,
        image_path: Path,
        output_path: Optional[Path] = None,
        total_detail: Optional[TotalScoreDetail] = None,
        values: Optional[Dict[str, float]] = None,
    ) -> Tuple[Path, List[BoardScoreDetail]]:
        if self.real_detections is None:
            self.real_detections = self._detect_reference_boards()

        sim_gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        sim_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if sim_gray is None or sim_bgr is None:
            raise RuntimeError(f"Failed reading screenshot: {image_path}")

        transform = self._get_eval_transform(sim_gray.shape[:2])
        sim_prepared = self._prepare_eval_image(sim_gray)
        palette = self._get_annotation_palette()
        board_scores: List[BoardScoreDetail] = []
        occupied_label_boxes: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []

        for index, board in enumerate(self.boards):
            color = palette[index % len(palette)]
            sim_detection = self._detect_board(sim_prepared, board)
            real_detection = self.real_detections[board.board_id]
            score = self._score_board(board, real_detection, sim_detection, sim_prepared)
            board_scores.append(score)

            if sim_detection.success and sim_detection.ordered_points.size > 0:
                mapped_points = self._map_eval_points_to_source(
                    sim_detection.ordered_points, transform
                ).reshape(-1, 2)
                mapped_points_int = np.round(mapped_points).astype(np.int32)
                checkerboard_outline = None
                if board.board_type == "checkerboard":
                    checkerboard_outline = self._checkerboard_outline(
                        mapped_points,
                        board.board_size,
                    )
                if checkerboard_outline is not None:
                    outline_int = np.round(checkerboard_outline).astype(np.int32).reshape(-1, 1, 2)
                    cv2.polylines(sim_bgr, [outline_int], True, color, 2, cv2.LINE_AA)
                    x, y, width, height = cv2.boundingRect(outline_int)
                elif mapped_points_int.shape[0] >= 3:
                    hull = cv2.convexHull(mapped_points_int.reshape(-1, 1, 2))
                    cv2.polylines(sim_bgr, [hull], True, color, 2, cv2.LINE_AA)
                    x, y, width, height = cv2.boundingRect(hull)
                else:
                    x, y, width, height = self._points_bbox(mapped_points)
                    cv2.rectangle(sim_bgr, (x, y), (x + width, y + height), color, 2)
                for point in mapped_points:
                    cv2.circle(
                        sim_bgr,
                        (int(round(float(point[0]))), int(round(float(point[1])))),
                        3,
                        color,
                        -1,
                    )
                label_anchor, label_box = self._resolve_annotated_label_anchor(
                    sim_bgr.shape,
                    board.board_id,
                    (x + 2, max(18, y - 10)),
                    occupied_label_boxes,
                )
                self._draw_annotated_label(
                    sim_bgr,
                    board.board_id,
                    label_anchor,
                    color,
                )
                occupied_label_boxes.append(label_box)
            elif board.roi is not None:
                rx, ry, rw, rh = board.roi
                sx = transform.scale_x
                sy = transform.scale_y
                ox = transform.offset_x
                oy = transform.offset_y
                src_x = int(round(rx / sx - ox / sx)) if sx > 0 else rx
                src_y = int(round(ry / sy - oy / sy)) if sy > 0 else ry
                src_w = int(round(rw / sx)) if sx > 0 else rw
                src_h = int(round(rh / sy)) if sy > 0 else rh
                cv2.rectangle(sim_bgr, (src_x, src_y), (src_x + src_w, src_y + src_h), color, 2)
                label_anchor, label_box = self._resolve_annotated_label_anchor(
                    sim_bgr.shape,
                    board.board_id,
                    (src_x + 2, max(18, src_y - 10)),
                    occupied_label_boxes,
                )
                self._draw_annotated_label(
                    sim_bgr,
                    board.board_id + "?",
                    label_anchor,
                    color,
                )
                occupied_label_boxes.append(label_box)

        total_detail = total_detail or self._aggregate_scores(board_scores, baseline_metrics=None)

        header = f"{Path(self.cfg['real_image']).name} compare on {image_path.name}"
        summary = (
            f"total={total_detail.total_score:.6f} "
            f"compared={total_detail.compared_board_count} "
            f"degrade={total_detail.degrade_penalty:.3f}"
        )
        isolated_outlier_text = None
        if total_detail.isolated_outlier_boards:
            isolated_outlier_text = "excluded=" + ", ".join(total_detail.isolated_outlier_boards)

        param_values = values or self._snapshot_values()
        info_lines: List[Tuple[str, Tuple[int, int, int]]] = [
            (header, (245, 245, 245)),
            (summary, (230, 235, 245)),
        ]
        if isolated_outlier_text:
            info_lines.append((isolated_outlier_text, (180, 180, 240)))
        info_lines.extend(
            (line, (220, 245, 220)) for line in self._format_value_lines(param_values)
        )
        legend_lines = _build_annotation_legend_lines(total_detail)
        for index, legend_text in enumerate(legend_lines):
            color = palette[index % len(palette)]
            info_lines.append((legend_text, color))

        font = cv2.FONT_HERSHEY_SIMPLEX
        panel_scale = 0.58
        panel_thickness = 2
        panel_gap = 24
        panel_left = 16
        panel_top = 24
        line_step = 30
        panel_min_width = 340
        max_text_width = 0
        for text, _ in info_lines:
            (text_w, _), _ = cv2.getTextSize(text, font, panel_scale, panel_thickness)
            max_text_width = max(max_text_width, text_w)
        panel_width = max(panel_min_width, max_text_width + 36)

        canvas_height, canvas_width = sim_bgr.shape[:2]
        annotated_canvas = np.full(
            (canvas_height, canvas_width + panel_gap + panel_width, 3),
            236,
            dtype=np.uint8,
        )
        annotated_canvas[:, :canvas_width] = sim_bgr
        separator_x = canvas_width + (panel_gap // 2)
        cv2.line(
            annotated_canvas,
            (separator_x, 0),
            (separator_x, canvas_height - 1),
            (205, 205, 205),
            2,
        )

        panel_x = canvas_width + panel_gap + panel_left
        panel_y = panel_top
        for text, color in info_lines:
            self._draw_annotated_label(
                annotated_canvas,
                text,
                (panel_x, panel_y),
                color,
                scale=panel_scale,
                thickness=panel_thickness,
            )
            panel_y += line_step

        final_output = output_path or image_path.with_name(f"{image_path.stem}_annotated.png")
        final_output.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(final_output), annotated_canvas)
        return final_output, total_detail.board_scores

    @staticmethod
    def _best_score_image_output_path(image_path: Path) -> Path:
        return image_path.with_name(f"{image_path.stem}_score.png")

    @staticmethod
    def _best_overlay_image_output_path(image_path: Path) -> Path:
        return image_path.with_name(f"{image_path.stem}_overlay.png")

    def _ensure_best_score_image(
        self,
        image_path: Path,
        total_detail: TotalScoreDetail,
        values: Optional[Dict[str, float]] = None,
    ) -> Optional[Path]:
        if not self.auto_generate_best_score_image:
            return None

        cache_key = str(image_path.resolve())
        output_path = self._best_score_image_output_path(image_path)
        cached = self._best_score_image_cache.get(cache_key)
        if cached is not None and cached.exists():
            return cached
        if output_path.exists():
            try:
                if output_path.stat().st_mtime >= image_path.stat().st_mtime:
                    self._best_score_image_cache[cache_key] = output_path
                    return output_path
            except OSError:
                pass

        annotated_path, _ = self.annotate_existing_image(
            image_path,
            output_path=output_path,
            total_detail=total_detail,
            values=values,
        )
        self._best_score_image_cache[cache_key] = annotated_path
        return annotated_path

    def _build_score_image_for_snapshot(
        self,
        image_path: Path,
        total_detail: TotalScoreDetail,
        values: Dict[str, float],
        output_path: Optional[Path] = None,
    ) -> Optional[Path]:
        if not self.auto_generate_best_score_image:
            return None

        final_output = output_path or self._best_score_image_output_path(image_path)
        annotated_path, _ = self.annotate_existing_image(
            image_path,
            output_path=final_output,
            total_detail=total_detail,
            values=values,
        )
        return annotated_path

    def _ensure_best_overlay_image(self, image_path: Path) -> Optional[Path]:
        if not self.auto_generate_best_overlay_image:
            return None

        cache_key = str(image_path.resolve())
        output_path = self._best_overlay_image_output_path(image_path)
        cached = self._best_overlay_image_cache.get(cache_key)
        if cached is not None and cached.exists():
            return cached
        if output_path.exists():
            try:
                if output_path.stat().st_mtime >= image_path.stat().st_mtime:
                    self._best_overlay_image_cache[cache_key] = output_path
                    return output_path
            except OSError:
                pass

        sim_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if sim_bgr is None:
            raise RuntimeError(f"Failed reading best image for overlay: {image_path}")
        sim_eval = self._prepare_eval_image(sim_bgr)
        real_eval = self._prepare_eval_image(self.real_img_color.copy())
        overlay = cv2.addWeighted(
            sim_eval,
            1.0 - self.overlay_visual_real_alpha,
            real_eval,
            self.overlay_visual_real_alpha,
            0.0,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), overlay, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        self._best_overlay_image_cache[cache_key] = output_path
        return output_path
