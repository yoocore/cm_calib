"""Trace the full detection pipeline to find RMSE root cause."""
from __future__ import annotations
import json, sys, math
from pathlib import Path
import cv2
import numpy as np

sys.path.insert(0, "E:/Coding/VibeCoding/cm_calib")
from src.calibration.calib_types import BoardProfile

real_path = "C:/CM_Projects/TM15.1_StreamaxCamera/Movie/frame_000001_raw.png.png"
sim_path  = "C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/calibration/VehSensor_0/rounds_20260630_165831/round_01/campaign/explore/start_00/initial.png"
cfg_path  = "C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json"

with open(cfg_path) as f:
    cfg = json.load(f)

bc = cfg["boards"][0]
board = BoardProfile(
    board_id=bc["board_id"],
    board_type=bc["board_type"],
    weight=bc.get("weight", 1.0),
    critical=bc.get("critical", False),
    roi=tuple(bc["roi"]),
    custom_detector=bc.get("custom_detector", "template_match"),
    template_match_threshold=bc.get("template_match_threshold", 0.45),
    template_binary_threshold=bc.get("template_binary_threshold", 150),
    min_detected_points=bc.get("min_detected_points", 9),
    template_source_roi=bc.get("template_source_roi"),
)

# Load ROI image from real image and extract template
real_bgr = cv2.imread(real_path, cv2.IMREAD_COLOR)
real_gray = cv2.cvtColor(real_bgr, cv2.COLOR_BGR2GRAY)
rx, ry, rw, rh = board.roi
roi_gray = real_gray[ry:ry+rh, rx:rx+rw]

tpl_path = "C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/templates/custom_1.png"
template_gray = cv2.imread(tpl_path, cv2.IMREAD_GRAYSCALE)

# --- Step 1: Compute content_bbox from OTSU (same as camera_calibration.py) ---
_, content_mask = cv2.threshold(template_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
content_points = np.column_stack(np.where(content_mask > 0))
if content_points.size > 0:
    min_y = int(np.min(content_points[:, 0]))
    max_y = int(np.max(content_points[:, 0]))
    min_x = int(np.min(content_points[:, 1]))
    max_x = int(np.max(content_points[:, 1]))
    content_bbox = (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
else:
    content_bbox = (0, 0, template_gray.shape[1], template_gray.shape[0])
print(f"template size: {template_gray.shape[1]}x{template_gray.shape[0]}")
print(f"OTSU content_bbox: {content_bbox}")

area_ratio = content_bbox[2] * content_bbox[3] / (template_gray.shape[1] * template_gray.shape[0])
print(f"content area ratio: {area_ratio:.3f}")
if area_ratio > 0.95:
    print("WARNING: content_bbox covers almost entire template — OTSU may not have isolated the marker")

# Show the OTSU mask
cv2.imwrite("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/diag_otsu_mask.png", content_mask)

# --- Step 2: Simulate reference detection from GEOMETRY (fallback path) ---
print(f"\n=== Reference detection from GEOMETRY ===")
ref_bbox = board.template_source_roi or board.roi
off_x_geom, off_y_geom, w_geom, h_geom = content_bbox
ref_bbox_adjusted = (
    int(round(ref_bbox[0] + off_x_geom)),
    int(round(ref_bbox[1] + off_y_geom)),
    max(1, int(round(w_geom))),
    max(1, int(round(h_geom))),
)
print(f"  original roi: {board.roi}")
print(f"  content_bbox: ({off_x_geom},{off_y_geom},{w_geom},{h_geom})")
print(f"  adjusted ref bbox: {ref_bbox_adjusted}")
print(f"  ref anchors (4 corners): ({ref_bbox_adjusted[0]},{ref_bbox_adjusted[1]}) ... ({ref_bbox_adjusted[0]+ref_bbox_adjusted[2]-1},{ref_bbox_adjusted[1]+ref_bbox_adjusted[3]-1})")

# --- Step 3: Simulate reference detection from TEMPLATE MATCH (success path) ---
print(f"\n=== Reference detection from TEMPLATE MATCH ===")
_, tpl_bin = cv2.threshold(template_gray, float(board.template_binary_threshold), 255, cv2.THRESH_BINARY_INV)
_, roi_bin = cv2.threshold(roi_gray, float(board.template_binary_threshold), 255, cv2.THRESH_BINARY_INV)
resp = cv2.matchTemplate(roi_bin, tpl_bin, cv2.TM_CCOEFF_NORMED)
_, val, _, loc = cv2.minMaxLoc(resp)
print(f"  NCC={val:.4f} threshold={board.template_match_threshold}")
print(f"  match offset in ROI: {loc}")
real_match_xy = (rx + loc[0], ry + loc[1])
print(f"  eval-space top-left: {real_match_xy}")

crop_x, crop_y = 0, 0
anchor_x = real_match_xy[0] - crop_x + content_bbox[0]
anchor_y = real_match_xy[1] - crop_y + content_bbox[1]
anchor_w = content_bbox[2]
anchor_h = content_bbox[3]
real_anchors = np.array([
    [anchor_x, anchor_y],
    [anchor_x + anchor_w - 1, anchor_y],
    [anchor_x + anchor_w - 1, anchor_y + anchor_h - 1],
    [anchor_x, anchor_y + anchor_h - 1],
], dtype=np.float32)
print(f"  content top-left: ({anchor_x}, {anchor_y})")
print(f"  content br: ({anchor_x + anchor_w - 1}, {anchor_y + anchor_h - 1})")

# --- Step 4: Simulate reference from template match CROPPED variant ---
print(f"\n=== Sim detection from TEMPLATE MATCH ===")
sim_bgr = cv2.imread(sim_path, cv2.IMREAD_COLOR)
sim_gray = cv2.cvtColor(sim_bgr, cv2.COLOR_BGR2GRAY)
target_h, target_w = real_bgr.shape[:2]
source_h, source_w = sim_bgr.shape[:2]
scale = min(target_w / max(1, source_w), target_h / max(1, source_h))
new_w = max(1, int(round(source_w * scale)))
new_h = max(1, int(round(source_h * scale)))
sim_resized = cv2.resize(sim_gray, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
canvas = np.zeros((target_h, target_w), dtype=sim_resized.dtype)
off_x = (target_w - new_w) // 2
off_y = (target_h - new_h) // 2
canvas[off_y:off_y+new_h, off_x:off_x+new_w] = sim_resized
sim_prepared = canvas

roi_sim = sim_prepared[ry:ry+rh, rx:rx+rw]
_, roi_sim_bin = cv2.threshold(roi_sim, float(board.template_binary_threshold), 255, cv2.THRESH_BINARY_INV)
resp_sim = cv2.matchTemplate(roi_sim_bin, tpl_bin, cv2.TM_CCOEFF_NORMED)
_, val_sim, _, loc_sim = cv2.minMaxLoc(resp_sim)
print(f"  NCC={val_sim:.4f} threshold={board.template_match_threshold}")
sim_match_xy = (rx + loc_sim[0], ry + loc_sim[1])
print(f"  eval-space top-left: {sim_match_xy}")

anchor_x_sim = sim_match_xy[0] - 0 + content_bbox[0]
anchor_y_sim = sim_match_xy[1] - 0 + content_bbox[1]
print(f"  content top-left: ({anchor_x_sim}, {anchor_y_sim})")

# --- Step 5: Compare geometry vs template match anchors ---
print(f"\n=== COMPARISON ===")
# Case A: reference from geometry, sim from template match
dx_A = anchor_x_sim - ref_bbox_adjusted[0]
dy_A = anchor_y_sim - ref_bbox_adjusted[1]
rmse_A = math.sqrt(dx_A**2 + dy_A**2)
print(f"Case A (ref=geometry, sim=tm): dx={dx_A}, dy={dy_A}, RMSE~={rmse_A:.1f}")

# Case B: reference from tm, sim from tm
dx_B = anchor_x_sim - anchor_x
dy_B = anchor_y_sim - anchor_y
rmse_B = math.sqrt(dx_B**2 + dy_B**2)
print(f"Case B (ref=tm, sim=tm):       dx={dx_B}, dy={dy_B}, RMSE~={rmse_B:.1f}")

# The actual RMSE from the run is 280.32
print(f"\nActual RMSE from result.json: 280.32")
if rmse_A > 200 or rmse_B > 200:
    print("✓ Match found — one scenario gives RMSE ~ 280")
else:
    print(f"Neither scenario gives RMSE ~ 280. Looking for other causes...")

# --- Step 6: Check if content_bbox is actually 0,0,template_w,template_h ---
# If OTSU doesn't isolate the content, content_bbox covers the full template
# But the code in _detect_template_match_board then shifts anchors by content_bbox offset
# which is (0,0) for full-template, so no shift.

# What if the cropped match_template _variant_ is used?
# The code tries match_template (cropped) first, then full template.
# The match_crop changes the anchor shift.

# Let me look for what _select_auto_template_crop does with this template
print(f"\n=== Checking cropped variant ===")
from src.calibration.config import _select_auto_template_crop
match_tpl, match_crop = _select_auto_template_crop(roi_gray, board.template_binary_threshold)
print(f"  match_template shape: {match_tpl.shape}")
print(f"  match_crop: {match_crop}")
_, tpl_crop_bin = cv2.threshold(match_tpl, float(board.template_binary_threshold), 255, cv2.THRESH_BINARY_INV)
resp_crop = cv2.matchTemplate(roi_bin, tpl_crop_bin, cv2.TM_CCOEFF_NORMED)
_, val_crop, _, loc_crop = cv2.minMaxLoc(resp_crop)
print(f"  NCC (cropped) on REAL: {val_crop:.4f} at ROI-offset={loc_crop}")

resp_sim_crop = cv2.matchTemplate(roi_sim_bin, tpl_crop_bin, cv2.TM_CCOEFF_NORMED)
_, val_sim_crop, _, loc_sim_crop = cv2.minMaxLoc(resp_sim_crop)
print(f"  NCC (cropped) on SIM:  {val_sim_crop:.4f} at ROI-offset={loc_sim_crop}")

# With crop variant:
if val_crop >= board.template_match_threshold:
    real_match_crop = (rx + loc_crop[0], ry + loc_crop[1])
    sim_match_crop = (rx + loc_sim_crop[0], ry + loc_sim_crop[1])
    # Anchors adjusted for crop
    content_bbox_local = content_bbox
    real_anchor_crop_x = real_match_crop[0] - match_crop[0] + content_bbox_local[0]
    real_anchor_crop_y = real_match_crop[1] - match_crop[1] + content_bbox_local[1]
    sim_anchor_crop_x = sim_match_crop[0] - match_crop[0] + content_bbox_local[0]
    sim_anchor_crop_y = sim_match_crop[1] - match_crop[1] + content_bbox_local[1]
    dx_C = sim_anchor_crop_x - real_anchor_crop_x
    dy_C = sim_anchor_crop_y - real_anchor_crop_y
    rmse_C = math.sqrt(dx_C**2 + dy_C**2)
    print(f"  Case C (crop variant): dx={dx_C}, dy={dy_C}, RMSE~={rmse_C:.1f}")
else:
    print("  Cropped variant below threshold on real image")
