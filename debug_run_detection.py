"""Run one-shot detection for mk_1 using actual calibration code path."""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import cv2

from src.calibration.config import CalibrationConfig
from src.calibration.scoring import CalibrationScorer
from src.calibration.camera_calibration import CameraCalibration

# Load config
config_path = "C:/CM_Projects/CMO1510_camera_calibration/Movie/calibtool_side_FL/camera.side_FL.json"

# Load real image for reference
real_img = cv2.imread("C:/CM_Projects/CMO1510_camera_calibration/Movie/calibtool_side_FL/cam_wing_left_front.png")
real_gray = cv2.cvtColor(real_img, cv2.COLOR_BGR2GRAY)

# Load SIM image (initial capture from calibration run)
sim_path = "C:/CM_Projects/CMO1510_camera_calibration/SimOutput/calibration/side_FL/rounds_20260701_000720/round_01/campaign/explore/start_00/initial.png"
sim_raw = cv2.imread(sim_path)
sim_gray = cv2.cvtColor(sim_raw, cv2.COLOR_BGR2GRAY)

# Create calibration instance (needed for template loading and detection)
calib = CameraCalibration(config_path, real_gray, comparison_mode="direct", keep_aspect_resize=True)

# Prepare eval image (this handles keep_aspect_resize)
eval_image = calib._prepare_eval_image(sim_gray)
print(f"eval_image: {eval_image.shape}")

# Get mk_1 board
mk1 = [b for b in calib.boards if b.board_id == "mk_1"][0]
print(f"mk_1: roi={mk1.roi}, type={mk1.board_type}")

# Get template info
template_info = calib.custom_templates.get("mk_1")
if template_info:
    print(f"template: {template_info['template'].shape}")
    print(f"  content_bbox: {template_info.get('content_bbox')}")
    if "match_template" in template_info:
        print(f"  match_template: {template_info['match_template'].shape}")
        print(f"  match_crop: {template_info.get('match_crop')}")

# Run reference detection
ref_det = calib._reference_detection_from_board_geometry(mk1)
if ref_det and ref_det.success:
    print(f"reference: {ref_det.point_count} points, center=({ref_det.ordered_points[4,0]:.1f},{ref_det.ordered_points[4,1]:.1f})")

# Run template matching detection on eval image
sim_det = calib._detect_template_match_board(eval_image, mk1)
if sim_det.success:
    print(f"detection: {sim_det.point_count} points, center=({sim_det.ordered_points[4,0]:.1f},{sim_det.ordered_points[4,1]:.1f})")
    # Compute RMSE
    deltas = sim_det.ordered_points - ref_det.ordered_points
    dists = np.linalg.norm(deltas, axis=1)
    rmse = float(np.sqrt(np.mean(np.square(dists))))
    print(f"RMSE: {rmse:.4f}")
else:
    print(f"detection FAILED: {sim_det.error_message}")

# Also test mk_2 and mk_3
for bid in ["mk_2", "mk_3"]:
    board = [b for b in calib.boards if b.board_id == bid][0]
    ref = calib._reference_detection_from_board_geometry(board)
    det = calib._detect_template_match_board(eval_image, board)
    if ref and ref.success and det and det.success:
        d = det.ordered_points - ref.ordered_points
        r = float(np.sqrt(np.mean(np.square(np.linalg.norm(d, axis=1)))))
        print(f"{bid}: RMSE={r:.4f}")
    elif det and not det.success:
        print(f"{bid}: FAILED - {det.error_message}")
