"""P4: Geometric sensitivity matrix for sparse scoring.

Pre-computes a sensitivity matrix mapping each parameter to each board's
influence, enabling evaluation to skip boards below a sensitivity threshold.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

from src.calibration.calib_types import ParameterSpec

if TYPE_CHECKING:
    from src.calibration.camera_calibration import CameraCalibrator


def build_geometric_sensitivity(
    boards: List,
    params: List[ParameterSpec],
    img_shape: Tuple[int, int],
) -> Dict[str, Dict[str, float]]:
    """Build sensitivity matrix: {param_name: {board_id: sensitivity [0,1]}}.

    Sensitivity estimates are geometric heuristics based on parameter type
    and board position relative to image centre.
    """
    centre = np.array([img_shape[1] / 2.0, img_shape[0] / 2.0])
    max_r = float(np.linalg.norm(centre))

    sens: Dict[str, Dict[str, float]] = {}
    for param in params:
        per_board: Dict[str, float] = {}
        pname = param.name.lower()
        for board in boards:
            if hasattr(board, "roi") and board.roi:
                cx = float(board.roi[0] + board.roi[2] / 2)
                cy = float(board.roi[1] + board.roi[3] / 2)
            else:
                cx, cy = centre[0], centre[1]
            norm_r = float(np.linalg.norm([cx - centre[0], cy - centre[1]])) / max(1.0, max_r)

            if "offset" in pname:
                per_board[board.board_id] = 1.0
            elif "fov" in pname:
                per_board[board.board_id] = 0.8 + 0.2 * norm_r
            elif "yaw" in pname or "mount_yaw" in pname:
                per_board[board.board_id] = 0.1 + 0.9 * abs(cx - centre[0]) / max(1.0, centre[0])
            elif "pitch" in pname or "mount_pitch" in pname:
                per_board[board.board_id] = 0.1 + 0.9 * abs(cy - centre[1]) / max(1.0, centre[1])
            elif "distortion" in pname:
                per_board[board.board_id] = 0.2 + 0.8 * norm_r
            else:
                per_board[board.board_id] = 1.0
        sens[param.name] = per_board
    return sens


