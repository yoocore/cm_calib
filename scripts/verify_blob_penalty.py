"""Verify: blob centroid residual in _custom_board_geometric_penalty."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path("C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0/camera.VehSensor_0.json")
OUT_DIR = Path("C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/verify_blob_penalty")
OUT_DIR.mkdir(parents=True, exist_ok=True)

PARAM_SETS = {
    "pool_best": {"yaw": 0.2502, "pos_x": 10.247, "pitch": 0.5088, "lens_fov": 120.0, "roll": -0.455, "pos_y": -0.127, "pos_z": 1.830, "lens_scale": 1.0, "lens_offset_x": 0.0, "lens_offset_y": 0.0},
    "current_best": {"yaw": 0.2054, "pos_x": 10.243, "pitch": 0.4487, "lens_fov": 120.0, "roll": -0.475, "pos_y": -0.129, "pos_z": 1.828, "lens_scale": 1.0, "lens_offset_x": 0.0, "lens_offset_y": 0.0},
    "custom": {"yaw": 0.1451, "pos_x": 10.233, "pitch": 0.3288, "lens_fov": 120.0, "roll": -0.475, "pos_y": -0.129, "pos_z": 1.828, "lens_scale": 1.0, "lens_offset_x": 0.0, "lens_offset_y": 0.0},
}

for label, params in PARAM_SETS.items():
    out_dir = OUT_DIR / label; out_dir.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    temp_cfg = _cfg_with_initial_values(cfg, params)
    temp_cfg["output_dir"] = str(out_dir)
    calib = CameraCalibrator(temp_cfg, config_path=CONFIG_PATH)
    calib._apply_initial_value_map_with_retry(params, label)
    calib.cfg["output_dir"] = str(out_dir)

    total_detail, sim_path = calib.evaluate("initial", baseline_metrics=None)
    bs = total_detail.board_scores[0]
    print(f"{label:<14} yaw={params['yaw']:.4f}  "
          f"RMSE={bs.rmse:.4f}  geom_penalty={bs.geometric_penalty:.4f}  "
          f"total_score={total_detail.total_score:.4f}")
    time.sleep(1)

print("\nDone.")
