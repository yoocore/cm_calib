"""Detailed evaluation of historical pool params vs current best."""
import json, sys, copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.calibration.orchestration import _cfg_with_initial_values
from src.calibration.camera_calibration import CameraCalibrator

CONFIG_PATH = Path(
    "C:/CM_Projects/TM15.1_StreamaxCamera/Movie/calibtool_VehSensor_0"
    "/camera.VehSensor_0.json"
)

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

with open(CONFIG_PATH, encoding="utf-8") as f:
    cfg = json.load(f)

def evaluate_detail(label: str, params: dict):
    temp_cfg = _cfg_with_initial_values(copy.deepcopy(cfg), params)
    calib = CameraCalibrator(temp_cfg, config_path=CONFIG_PATH)
    calib._apply_initial_value_map_with_retry(params, f"eval_{label}")
    detail, img_path = calib.evaluate("eval", baseline_metrics=None)
    print(f"\n=== {label} ===")
    print(f"  total_score={detail.total_score!r}")
    print(f"  compared_board_count={detail.compared_board_count}")
    print(f"  degrade_penalty={detail.degrade_penalty!r}")
    for s in detail.board_scores:
        print(f"  board {s.board_id} ({s.board_type}):")
        print(f"    score={s.total_score!r}")
        print(f"    rmse={s.rmse!r}")
        print(f"    mean_error={s.mean_error!r}")
        print(f"    max_error={s.max_error!r}")
        print(f"    miss_rate={s.miss_rate}")
        print(f"    matched_point_count={s.matched_point_count}")
        print(f"    compared={s.compared}, reference_visible={s.reference_visible}, sim_visible={s.sim_visible}")
        if hasattr(s, 'geometric_penalty'):
            print(f"    geometric_penalty={s.geometric_penalty}")

for name, params in [("pool_best (yaw=0.2502)", pool_params), ("current_best (yaw=0.2054)", current_params)]:
    evaluate_detail(name, params)
