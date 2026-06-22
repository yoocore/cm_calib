"""Minimal test: try a single capture to verify CheckViewPort doesn't error."""
import sys, os, time
sys.path.insert(0, ".")
from src.calibration.camera_calibration import CameraCalibrator
import json

# Load config
config_path = "configs/camera.rear_tv.json"
with open(config_path) as f:
    cfg = json.load(f)

# Create calibrator
calib = CameraCalibrator(cfg)

# Try a single capture
print("Attempting single capture...")
try:
    result = calib._capture_movie_via_dde("test_capture")
    print(f"Capture result: {result}")
    print("SUCCESS: No CheckViewPort error!")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    if "CheckViewPort" in str(e):
        print("FAILED: CheckViewPort error still occurs!")
    else:
        print("Different error (not CheckViewPort)")
