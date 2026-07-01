"""Compare overlay for pool params vs current params side by side."""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path(
    "C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0"
    "/camera.VehSensor_0.json"
)
TEMP_DIR = Path(
    "C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/param_compare"
)

with open(CONFIG_PATH, encoding="utf-8") as f:
    cfg = json.load(f)

pool_params = {
    "yaw": 0.2502, "pos_x": 10.247, "pitch": 0.5088, "lens_fov": 120.0,
    "roll": -0.455, "pos_y": -0.127, "pos_z": 1.83,
    "lens_scale": 1.0, "lens_offset_x": 0.0, "lens_offset_y": 0.0,
}
current_params = {
    "yaw": 0.2054, "pos_x": 10.243, "pitch": 0.4487, "lens_fov": 120.0,
    "roll": -0.475, "pos_y": -0.129, "pos_z": 1.828,
    "lens_scale": 1.0, "lens_offset_x": 0.0, "lens_offset_y": 0.0,
}

def run(label: str, params: dict):
    out_dir = TEMP_DIR / label
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_cfg = _cfg_with_initial_values(cfg, params)
    temp_cfg["output_dir"] = str(out_dir)
    calib = CameraCalibrator(temp_cfg, config_path=CONFIG_PATH)
    calib._apply_initial_value_map_with_retry(params, label)

    calib.cfg["output_dir"] = str(out_dir)
    detail, best_img = calib.evaluate("initial", baseline_metrics=None)
    # Generate overlay image
    overlay = calib._ensure_best_overlay_image(best_img)
    print(f"[{label}] score={detail.total_score!r}")
    print(f"  overlay={overlay}")
    for f in out_dir.iterdir():
        if f.suffix in (".png",):
            print(f"  {f.name}")
    return detail

print("=== 1/2: pool params (yaw=0.2502) ===")
run("pool_best", pool_params)

print("\n=== 2/2: current params (yaw=0.2054) ===")
run("current_best", current_params)

print(f"\nOverlay images saved to: {TEMP_DIR}")
print("View: initial_overlay.png from each subfolder")
