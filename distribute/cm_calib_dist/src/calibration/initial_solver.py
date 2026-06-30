"""P3: Initial solver — estimate parameter values from initial corner displacements.

Uses 5 anchor boards (4 corners + centre) to produce rough estimates for
offset, fov, yaw, and pitch parameters.
"""

from __future__ import annotations
from typing import Dict, List, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from src.calibration.camera_calibration import CameraCalibrator


def _select_anchors(boards: List) -> List[str]:
    """Select anchor board IDs: corner boards + centre board.

    Priority: cb_0, cb_2, cb_4, cb_7, cb_8, or first 5 boards.
    """
    preferred = ["cb_0", "cb_2", "cb_4", "cb_7", "cb_8",
                 "B0", "B2", "B4", "B7", "B8",
                 "G1_LEFT", "G1_CENTER", "G1_RIGHT",
                 "S0", "S2", "S4", "S7", "S8"]
    available = {b.board_id for b in boards}
    anchors = [bid for bid in preferred if bid in available]
    if len(anchors) < 5:
        anchors = list(available)[:5]
    return anchors


class InitialSolver:
    """Estimates starting parameter values from initial board detection geometry."""

    @staticmethod
    def estimate(
        calibrator: CameraCalibrator,
        corner_snapshot: Optional[dict] = None,
    ) -> Optional[Dict[str, float]]:
        boards = calibrator.boards
        if not boards or len(boards) < 3:
            return None

        # Get or build corner displacement snapshot
        if corner_snapshot is None:
            disp: Dict[str, np.ndarray] = {}
            radii: Dict[str, float] = {}
            img_shape = calibrator.real_img.shape[:2]
            centre = np.array([img_shape[1] / 2, img_shape[0] / 2])
            for b in boards:
                if hasattr(b, "_last_detection") and b._last_detection:
                    det = b._last_detection
                    if hasattr(det, "corner_displacements") and det.corner_displacements is not None:
                        disp[b.board_id] = np.array(det.corner_displacements)
                    if hasattr(det, "detected_corners") and det.detected_corners is not None:
                        corners = np.array(det.detected_corners)
                        if len(corners) > 0:
                            cx, cy = corners[:, 0].mean(), corners[:, 1].mean()
                            radii[b.board_id] = max(1.0, float(np.linalg.norm([cx - centre[0], cy - centre[1]])))
        else:
            disp = corner_snapshot.get("displacements", {})
            radii = corner_snapshot.get("radii", {})

        if not disp:
            return None

        anchors = _select_anchors(boards)
        active = {bid: d for bid, d in disp.items() if bid in anchors and len(d) > 0}
        if len(active) < 3:
            return None

        result: Dict[str, float] = {}

        # offset_x/y: mean displacement across anchors
        all_disps = np.vstack(list(active.values()))
        mean_dx = float(np.mean(all_disps[:, 0]))
        mean_dy = float(np.mean(all_disps[:, 1]))
        result["lens_sensor_offset_x"] = mean_dx
        result["lens_sensor_offset_y"] = mean_dy

        # fov: radial scaling — avg(radius / displacement_radius)
        max_r = max(radii.values()) if radii else 1.0
        if radii and max_r > 0:
            fov_ratios = []
            for bid, d in active.items():
                if bid in radii:
                    mean_disp_mag = float(np.linalg.norm(np.mean(d, axis=0)))
                    fov_ratios.append(radii[bid] / max(1.0, mean_disp_mag + radii[bid]))
            if fov_ratios:
                fov_scale = float(np.mean(fov_ratios))
                # Map to reasonable fov range via heuristic
                for p in calibrator.params:
                    if p.name == "lens_fov":
                        base = p.initial
                        result["lens_fov"] = base * (1.0 + 0.3 * (fov_scale - 0.5))
                        break

        # yaw: left-right asymmetry
        left_x = []
        right_x = []
        centre_x = float(img_shape[1] / 2)
        for bid, d in active.items():
            if "LEFT" in bid.upper() or bid.startswith("cb_0") or bid.startswith("B0"):
                left_x.append(float(np.mean(d[:, 0])))
            elif "RIGHT" in bid.upper() or bid.startswith("cb_4") or bid.startswith("B4"):
                right_x.append(float(np.mean(d[:, 0])))
        if left_x and right_x:
            asym_x = float(np.mean(right_x) - np.mean(left_x))
            for p in calibrator.params:
                if p.name == "mount_yaw":
                    result["mount_yaw"] = asym_x * 0.01
                    break

        # pitch: top-bottom asymmetry
        top_y = []
        bottom_y = []
        centre_y = float(img_shape[0] / 2)
        for bid, d in active.items():
            for board in boards:
                if board.board_id == bid:
                    cy = board.roi[1] + board.roi[3] / 2 if hasattr(board, "roi") else centre_y
                    if cy < centre_y:
                        top_y.append(float(np.mean(d[:, 1])))
                    else:
                        bottom_y.append(float(np.mean(d[:, 1])))
                    break
        if top_y and bottom_y:
            asym_y = float(np.mean(bottom_y) - np.mean(top_y))
            for p in calibrator.params:
                if p.name == "mount_pitch":
                    result["mount_pitch"] = asym_y * 0.01
                    break

        return result if result else None
