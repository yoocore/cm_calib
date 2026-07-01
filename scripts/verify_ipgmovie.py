"""Apply pos_x=11 to verify IPGMovie responds to Script Control."""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path(
    "C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0"
    "/camera.VehSensor_0.json"
)

with open(CONFIG_PATH, encoding="utf-8") as f:
    cfg = json.load(f)

params = {
    "yaw": 0.2054, "pos_x": 11.0, "pitch": 0.4487, "lens_fov": 120.0,
    "roll": -0.475, "pos_y": -0.129, "pos_z": 1.828,
    "lens_scale": 1.0, "lens_offset_x": 0.0, "lens_offset_y": 0.0,
}

temp_cfg = _cfg_with_initial_values(cfg, params)
calib = CameraCalibrator(temp_cfg, config_path=CONFIG_PATH)
calib._apply_initial_value_map_with_retry(params, "verify_dde")
print("DONE: pos_x=11.0 applied via Script Control.")
print("现在看 Movie 画面，位置应该明显偏了（pos_x 从 10.24 改成 11.0）")
