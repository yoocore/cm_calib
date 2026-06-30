"""Full calibration pipeline diagnosis — check template loading and detection."""
from __future__ import annotations
import json, sys
from pathlib import Path
import cv2
import numpy as np

sys.path.insert(0, "E:/Coding/VibeCoding/cm_calib")

from src.calibration.config import _bootstrap_partial_template_dir, _materialize_auto_template_image

real_path = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/frame_000001_raw.png.png")
camera_name = "VehSensor_0"

# Check bootstrap template dir
template_dir = _bootstrap_partial_template_dir(real_path, camera_name)
print(f"Bootstrap template dir: {template_dir}")
print(f"  exists: {template_dir.exists()}")

# Check for auto-generated templates
if template_dir.exists():
    for f in template_dir.iterdir():
        print(f"  {f.name} ({f.stat().st_size} bytes)")
else:
    print("  NOT FOUND - templates were NOT generated!")

# Manually extract the template from the ROI
real_gray = cv2.imread(str(real_path), cv2.IMREAD_GRAYSCALE)
roi = (673, 405, 497, 253)
x, y, w, h = roi
roi_gray = real_gray[y:y+h, x:x+w]
print(f"\nROI image: {roi_gray.shape}")

# Try select_auto_template_crop
from src.calibration.config import _select_auto_template_crop
template, crop = _select_auto_template_crop(roi_gray, 150)
print(f"Auto template: {template.shape}, crop: {crop}")

# Now simulate what _materialize_custom_maker_templates does
manual_crop = (0, 0, w, h)
template_path = template_dir / "mk_1_auto.png"
print(f"\nWould save template to: {template_path}")
template_path.parent.mkdir(parents=True, exist_ok=True)
saved_path, saved_crop = _materialize_auto_template_image(
    real_gray, roi, 150, template_path, manual_crop=manual_crop
)
print(f"Saved template: {saved_path} ({saved_path.stat().st_size} bytes)")
print(f"Template shape: {cv2.imread(str(saved_path), cv2.IMREAD_GRAYSCALE).shape}")
print(f"Template crop: {saved_crop}")

# Now load it like _load_custom_templates does
from PIL import Image
tpl_loaded = cv2.imread(str(saved_path), cv2.IMREAD_GRAYSCALE)
print(f"\nLoaded template: {tpl_loaded.shape}")

# Compute content_bbox via OTSU
_, content_mask = cv2.threshold(tpl_loaded, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
content_pts = np.column_stack(np.where(content_mask > 0))
if content_pts.size > 0:
    min_y = int(np.min(content_pts[:, 0]))
    max_y = int(np.max(content_pts[:, 0]))
    min_x = int(np.min(content_pts[:, 1]))
    max_x = int(np.max(content_pts[:, 1]))
    content_bbox = (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
    print(f"Content bbox (OTSU): {content_bbox}")
    print(f"  = ({min_x},{min_y}) to ({max_x},{max_y}) of template {tpl_loaded.shape[1]}x{tpl_loaded.shape[0]}")

    area_ratio = content_bbox[2] * content_bbox[3] / (tpl_loaded.shape[1] * tpl_loaded.shape[0])
    print(f"  content area ratio: {area_ratio:.3f}")
else:
    print("NO content points found from OTSU!")
