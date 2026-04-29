import argparse
import atexit
import ctypes
import json
import math
import sys
import time
import warnings
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, TextIO, Tuple

import cv2
import numpy as np
from PIL import Image
from PIL import ImageGrab
from pywinauto import Application
from pywinauto import Desktop
from pywinauto import mouse
from pywinauto.keyboard import send_keys
from pywinauto.timings import TimeoutError as PywinautoTimeoutError


BM_CLICK = 0x00F5


class _TeeStream:
    def __init__(self, primary: TextIO, secondary: TextIO):
        self._primary = primary
        self._secondary = secondary

    def write(self, data: str) -> int:
        written = self._primary.write(data)
        self._secondary.write(data)
        self.flush()
        return written

    def flush(self) -> None:
        self._primary.flush()
        try:
            self._secondary.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._primary.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return self._primary.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._primary, "encoding", "utf-8")

    def reconfigure(self, *args, **kwargs) -> None:
        if hasattr(self._primary, "reconfigure"):
            self._primary.reconfigure(*args, **kwargs)
        if hasattr(self._secondary, "reconfigure"):
            self._secondary.reconfigure(*args, **kwargs)


def _cleanup_live_log(primary_stdout: TextIO, primary_stderr: TextIO, log_stream: TextIO) -> None:
    sys.stdout = primary_stdout
    sys.stderr = primary_stderr
    try:
        log_stream.flush()
    except Exception:
        pass
    try:
        log_stream.close()
    except Exception:
        pass


def _configure_live_log(cfg: dict, resume_from_result: bool) -> Path:
    output_dir = _resolve_config_output_dir(cfg)
    cfg["output_dir"] = str(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_name = "continue_resume.log" if resume_from_result else "run.log"
    log_path = output_dir / log_name
    log_stream = open(log_path, "w", encoding="utf-8", buffering=1)
    primary_stdout = sys.stdout
    primary_stderr = sys.stderr
    atexit.register(_cleanup_live_log, primary_stdout, primary_stderr, log_stream)
    sys.stdout = _TeeStream(primary_stdout, log_stream)
    sys.stderr = _TeeStream(primary_stderr, log_stream)
    return log_path


def _default_sim_output_root() -> Path:
    return Path("C:/CM_Projects/CMO141_Calibration/SimOutput")


def _default_output_name_from_config(config_path: Optional[Path]) -> str:
    if config_path is not None:
        name = config_path.stem
        prefix = "config."
        if name.startswith(prefix):
            name = name[len(prefix):]
        name = name.replace(".", "_").strip("_")
        if name:
            return name
    return "camera_calibration_run"


def _resolve_config_output_dir(cfg: dict, config_path: Optional[Path] = None) -> Path:
    raw_output_dir = str(cfg.get("output_dir", "")).strip()
    if raw_output_dir:
        return Path(raw_output_dir)
    return _default_sim_output_root() / _default_output_name_from_config(config_path)


def _build_isolated_output_dir(prefix: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return _default_sim_output_root() / f"{prefix}_{ts}"


def _marker_name_for_output_dir(output_dir: Path) -> str:
    return f"{output_dir.name}_last.json"


def _marker_path_for_output_dir(output_dir: Path) -> Path:
    return _default_sim_output_root() / _marker_name_for_output_dir(output_dir)


def _write_run_marker(marker_path: Path, payload: dict) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_latest_result_path(marker_path: Path, fallback_output_dir: Path) -> Path:
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        return fallback_output_dir / "result.json"
    result_json = marker.get("result_json")
    if isinstance(result_json, str) and result_json.strip():
        return Path(result_json)
    return fallback_output_dir / "result.json"


@dataclass
class ParameterSpec:
    name: str
    value: float
    step: float
    min_value: float
    max_value: float
    min_step: float
    decimals: int
    field_index: Optional[int] = None
    auto_id: Optional[str] = None
    title: Optional[str] = None
    click_x: Optional[int] = None
    click_y: Optional[int] = None


@dataclass
class BoardProfile:
    board_id: str
    board_type: str
    weight: float
    critical: bool
    roi: Optional[Tuple[int, int, int, int]]
    board_size: Optional[Tuple[int, int]] = None
    square_size: float = 1.0
    alpha: float = 1000.0
    beta: float = 0.1
    fail_penalty: float = 1e6
    min_detected_points: int = 1
    degrade_threshold_rmse: float = float("inf")
    degrade_threshold_max_error: float = float("inf")
    degrade_threshold_miss_rate: float = float("inf")
    template_image: Optional[str] = None
    min_match_count: int = 20
    custom_detector: str = "feature"
    template_match_threshold: float = 0.0
    template_binary_threshold: int = 0
    template_crop: Optional[Tuple[int, int, int, int]] = None


@dataclass
class DetectionResult:
    board_id: str
    success: bool
    point_count: int
    ordered_points: np.ndarray
    board_type: str
    roi_used: Optional[Tuple[int, int, int, int]] = None
    detector: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class BoardScoreDetail:
    board_id: str
    board_type: str
    success: bool
    compared: bool
    reference_visible: bool
    sim_visible: bool
    total_score: float
    rmse: float
    mean_error: float
    max_error: float
    miss_rate: float
    matched_point_count: int
    failed_reason: Optional[str] = None


@dataclass
class TotalScoreDetail:
    success: bool
    total_score: float
    degrade_penalty: float
    has_critical_degrade: bool
    degraded_boards: List[str]
    compared_board_count: int
    board_scores: List[BoardScoreDetail]
    failed_reason: Optional[str] = None


@dataclass
class EvalImageTransform:
    scale_x: float
    scale_y: float
    offset_x: int
    offset_y: int
    eval_width: int
    eval_height: int
    source_width: int
    source_height: int


class CameraCalibrator:
    SCRIPT_CONTROL_WRITE_WIDGETS = {
        "roll": ".camera.presetFrame.x",
        "pitch": ".camera.presetFrame.y",
        "yaw": ".camera.presetFrame.z",
        "pos_x": ".camera.presetFrame.evptx",
        "pos_y": ".camera.presetFrame.evpty",
        "pos_z": ".camera.presetFrame.evptz",
        "fov": ".camera.fovFrame.efov",
        "lens_fov": ".camera.cammoddlg.fov.e",
        "lens_scale": ".camera.cammoddlg.fisheye.ctrl.e1",
        "lens_offset_x": ".camera.cammoddlg.fisheye.ctrl.e2",
        "lens_offset_y": ".camera.cammoddlg.fisheye.ctrl.e3",
    }
    SCRIPT_CONTROL_READ_WIDGETS = {
        "roll": ".camera.presetFrame.x",
        "pitch": ".camera.presetFrame.y",
        "yaw": ".camera.presetFrame.z",
        "pos_x": ".camera.presetFrame.svptx",
        "pos_y": ".camera.presetFrame.svpty",
        "pos_z": ".camera.presetFrame.svptz",
        "fov": ".camera.fovFrame.efov",
        "lens_fov": ".camera.cammoddlg.fov.e",
        "lens_scale": ".camera.cammoddlg.fisheye.ctrl.e1",
        "lens_offset_x": ".camera.cammoddlg.fisheye.ctrl.e2",
        "lens_offset_y": ".camera.cammoddlg.fisheye.ctrl.e3",
    }
    SCRIPT_CONTROL_READ_DECIMALS = {
        "pos_x": 3,
        "pos_y": 3,
        "pos_z": 3,
    }

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.app = None
        self.win32_app = None
        self.script_control_app = None
        self.movie_win = None
        self.settings_win = None
        self.script_control_win = None
        self.settings_backend = "uia"
        self._win32_handle_cache: Dict[str, int] = {}
        self._auto_param_handle_cache: Optional[Dict[str, int]] = None
        self._auto_param_handle_status_logged = False
        self.repo_root = Path(__file__).resolve().parents[3]
        self.output_dir = _resolve_config_output_dir(cfg)
        cfg["output_dir"] = str(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.real_img = cv2.imread(cfg["real_image"], cv2.IMREAD_GRAYSCALE)
        if self.real_img is None:
            raise FileNotFoundError(f"Cannot read real image: {cfg['real_image']}")

        self.orb = cv2.ORB_create(nfeatures=3000)
        self.params = self._load_params(cfg["parameters"])
        self.params = self._order_params(self.params, cfg.get("optimization_order"))

        self.settle_sec = float(cfg.get("settle_sec", 0.3))
        self.target_score = float(cfg.get("target_score", 5.0))
        self.max_iters = int(cfg.get("max_iters", 100))
        self.min_improve = float(cfg.get("min_improve", 1e-4))
        self.step_decay = float(cfg.get("step_decay", 0.6))
        self.settings_input_mode = str(cfg.get("settings_input_mode", "script_control")).lower()
        if self.settings_input_mode != "script_control":
            raise ValueError("Only settings_input_mode='script_control' is supported")
        script_root = self.repo_root / "Data" / "Script"
        calibration_root = Path(__file__).resolve().parent
        default_runtime_path = calibration_root / "script_control_runtime.tcl"
        default_command_path = calibration_root / "script_control_apply.tcl"
        default_result_path = self.repo_root / "SimOutput" / "script_control_camera_apply_result.txt"
        self.script_control_window_title_re = str(
            cfg.get("script_control_window_title_re", ".*Script Control.*")
        )
        configured_script_path = Path(cfg.get("script_control_script_path", str(default_command_path)))
        if not configured_script_path.is_absolute():
            configured_script_path = (self.repo_root / configured_script_path).resolve()
        self.script_control_script_path = configured_script_path
        configured_runtime_path = Path(
            cfg.get("script_control_runtime_path", str(default_runtime_path))
        )
        if not configured_runtime_path.is_absolute():
            configured_runtime_path = (self.repo_root / configured_runtime_path).resolve()
        self.script_control_runtime_path = configured_runtime_path
        if configured_runtime_path == script_root or script_root in configured_runtime_path.parents:
            self.script_control_browser_path = configured_runtime_path.relative_to(
                script_root
            ).as_posix()
        else:
            self.script_control_browser_path = str(configured_runtime_path)
        self.script_control_result_path = Path(
            cfg.get("script_control_result_path", str(default_result_path))
        )
        self.script_control_execute_mode = str(
            cfg.get("script_control_execute_mode", "console")
        ).lower()
        self.script_control_prefer_dde = bool(cfg.get("script_control_prefer_dde", True))
        self.script_control_dde_service = str(cfg.get("script_control_dde_service", "TclEval"))
        self.script_control_dde_topic = str(cfg.get("script_control_dde_topic", "CarMaker"))
        self.script_control_console_click_x = int(cfg.get("script_control_console_click_x", 22))
        self.script_control_console_click_y_from_bottom = int(
            cfg.get("script_control_console_click_y_from_bottom", 42)
        )
        self.script_control_start_click_x = cfg.get("script_control_start_click_x")
        self.script_control_start_click_y = cfg.get("script_control_start_click_y")
        self.script_control_start_click_x_from_right = cfg.get(
            "script_control_start_click_x_from_right"
        )
        self.script_control_start_click_y_from_bottom = cfg.get(
            "script_control_start_click_y_from_bottom"
        )
        self.script_control_timeout_sec = float(cfg.get("script_control_timeout_sec", 5.0))
        self.script_control_manual_start_timeout_sec = float(
            cfg.get("script_control_manual_start_timeout_sec", 0.0)
        )
        self.script_control_settle_sec = float(cfg.get("script_control_settle_sec", 0.2))
        self.script_control_prefer_background_start = bool(
            cfg.get("script_control_prefer_background_start", False)
        )
        self.script_control_allow_physical_fallback = bool(
            cfg.get("script_control_allow_physical_fallback", True)
        )
        self._script_control_runtime_loaded = False
        if self.settings_input_mode == "script_control":
            if (
                self.script_control_execute_mode != "dde"
                and (self.script_control_start_click_x is None or self.script_control_start_click_y is None)
            ):
                raise ValueError(
                    "script_control_start_click_x and script_control_start_click_y are required "
                    "when settings_input_mode='script_control' and script_control_execute_mode != 'dde'"
                )
        self.field_settle_sec = float(cfg.get("field_settle_sec", 0.08))
        self.template_feature_max_dim = int(cfg.get("template_feature_max_dim", 2048))
        self.movie_capture_mode = str(cfg.get("movie_capture_mode", "client")).lower()
        if self.movie_capture_mode not in {"window", "client", "dde_fbo"}:
            raise ValueError("movie_capture_mode must be 'window', 'client', or 'dde_fbo'")
        self.movie_auto_crop_content = bool(cfg.get("movie_auto_crop_content", True))
        self.movie_match_reference_aspect = bool(
            cfg.get("movie_match_reference_aspect", True)
        )
        self.comparison_mode = str(cfg.get("comparison_mode", "direct")).lower()
        if self.comparison_mode not in {"direct", "overlay_residual"}:
            raise ValueError("comparison_mode must be 'direct' or 'overlay_residual'")
        self.overlay_residual_threshold = int(cfg.get("overlay_residual_threshold", 12))
        self.overlay_residual_blur = int(cfg.get("overlay_residual_blur", 0))
        self.movie_aspect_crop_anchor = str(
            cfg.get("movie_aspect_crop_anchor", "top-left")
        ).lower()
        if self.movie_aspect_crop_anchor not in {"top-left", "center"}:
            raise ValueError("movie_aspect_crop_anchor must be 'top-left' or 'center'")
        self.verify_all_coordinate_fields = bool(
            cfg.get("verify_all_coordinate_fields", True)
        )
        self.stop_after_first_accepted_direction = bool(
            cfg.get("stop_after_first_accepted_direction", True)
        )
        priority_accept_cfg = cfg.get("priority_board_acceptance", {})
        self.priority_board_accept_ids = [
            str(board_id).strip()
            for board_id in priority_accept_cfg.get("board_ids", [])
            if str(board_id).strip()
        ]
        self.priority_board_accept_min_improvement = float(
            priority_accept_cfg.get("min_board_score_improvement", 0.0)
        )
        self.priority_board_accept_max_total_worsen = float(
            priority_accept_cfg.get("max_total_score_worsen", 0.0)
        )
        self.priority_board_accept_min_count = max(
            1, int(priority_accept_cfg.get("min_board_count", 1))
        )
        self.degrade_lambda = float(cfg.get("degrade_lambda", 100.0))
        self.compare_only_if_reference_visible = bool(
            cfg.get("compare_only_if_reference_visible", True)
        )
        self.no_signal_penalty = float(cfg.get("no_signal_penalty", 1e5))
        self.progress_flush_every = max(1, int(cfg.get("progress_flush_every", 1)))
        self.movie_content_crop = cfg.get("movie_content_crop")
        self.movie_dde_content_crop = cfg.get("movie_dde_content_crop")
        self.keep_aspect_resize = bool(cfg.get("keep_aspect_resize", True))
        joint_exploration_cfg = cfg.get("joint_exploration", {})
        self.joint_exploration_param_names = [
            str(name).strip()
            for name in joint_exploration_cfg.get("param_names", [])
            if str(name).strip()
        ]
        self.joint_exploration_param_set = set(self.joint_exploration_param_names)
        self.joint_exploration_max_single_worsen = float(
            joint_exploration_cfg.get("max_single_score_worsen", 0.0)
        )
        raw_trial_multipliers = joint_exploration_cfg.get("trial_multipliers", [1.0])
        trial_multipliers: List[float] = []
        for raw_value in raw_trial_multipliers:
            try:
                multiplier = abs(float(raw_value))
            except (TypeError, ValueError):
                continue
            if multiplier <= 0.0:
                continue
            if any(math.isclose(multiplier, seen, rel_tol=0.0, abs_tol=1e-12) for seen in trial_multipliers):
                continue
            trial_multipliers.append(multiplier)
        self.joint_exploration_trial_multipliers = trial_multipliers or [1.0]
        self.param_order_index = {param.name: index for index, param in enumerate(self.params)}
        self.preferred_directions = {param.name: 1.0 for param in self.params}
        self.resume_result_path: Optional[Path] = None
        self.resume_best_score: Optional[float] = None
        self.live_log_path: Optional[Path] = None
        self.run_session_id = uuid.uuid4().hex
        self.run_started_at = datetime.now().astimezone().isoformat(timespec="seconds")

        self.boards = self._load_boards(cfg.get("boards", []))
        if not self.boards:
            raise ValueError("boards must be a non-empty array")

        self.custom_templates = self._load_custom_templates(self.boards)
        self.real_detections: Optional[Dict[str, DetectionResult]] = None

    @staticmethod
    def _read_clipboard_text() -> str:
        # Prefer pywin32 clipboard API when available.
        text = ""
        try:
            import win32clipboard  # type: ignore

            for _ in range(3):
                try:
                    win32clipboard.OpenClipboard()
                    data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    text = data if isinstance(data, str) else str(data)
                    win32clipboard.CloseClipboard()
                    return text.strip()
                except Exception:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass
                    time.sleep(0.05)
            return text.strip()
        except Exception:
            pass

        # Fallback via Win32 APIs.
        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        for _ in range(3):
            if user32.OpenClipboard(None):
                try:
                    handle = user32.GetClipboardData(CF_UNICODETEXT)
                    if handle:
                        ptr = kernel32.GlobalLock(handle)
                        if ptr:
                            try:
                                text = ctypes.wstring_at(ptr)
                                return text.strip()
                            finally:
                                kernel32.GlobalUnlock(handle)
                finally:
                    user32.CloseClipboard()
            time.sleep(0.05)
        return text.strip()

    @staticmethod
    def _clear_clipboard_text() -> None:
        try:
            import win32clipboard  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
            finally:
                win32clipboard.CloseClipboard()
            return
        except Exception:
            pass

    @staticmethod
    def _set_clipboard_text(text: str) -> None:
        try:
            import win32clipboard  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return
        except Exception:
            pass

        GMEM_MOVEABLE = 0x0002
        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        data = ctypes.create_unicode_buffer(text + "\0")
        byte_size = ctypes.sizeof(data)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, byte_size)
        if not handle:
            raise RuntimeError("GlobalAlloc failed while setting clipboard text")
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            raise RuntimeError("GlobalLock failed while setting clipboard text")
        try:
            ctypes.memmove(ptr, ctypes.addressof(data), byte_size)
        finally:
            kernel32.GlobalUnlock(handle)

        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            raise RuntimeError("OpenClipboard failed while setting clipboard text")
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise RuntimeError("SetClipboardData failed while setting clipboard text")
            handle = None
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

        try:
            user32 = ctypes.windll.user32
            if user32.OpenClipboard(None):
                try:
                    user32.EmptyClipboard()
                finally:
                    user32.CloseClipboard()
        except Exception:
            pass

    @staticmethod
    def _is_visible(detection: DetectionResult, min_points: int) -> bool:
        return bool(detection.success and detection.point_count >= min_points)

    @staticmethod
    def _order_params(
        params: List[ParameterSpec], optimization_order: Optional[List[str]]
    ) -> List[ParameterSpec]:
        if not optimization_order:
            return params

        param_map = {param.name: param for param in params}
        ordered: List[ParameterSpec] = []
        used = set()
        for name in optimization_order:
            if name in param_map:
                ordered.append(param_map[name])
                used.add(name)
        for param in params:
            if param.name not in used:
                ordered.append(param)
        return ordered

    @staticmethod
    def _parse_roi(roi_cfg: Optional[List[int]]) -> Optional[Tuple[int, int, int, int]]:
        if roi_cfg is None:
            return None
        if not isinstance(roi_cfg, list) or len(roi_cfg) != 4:
            raise ValueError("roi must be [x, y, width, height]")
        x, y, width, height = [int(v) for v in roi_cfg]
        if width <= 0 or height <= 0:
            raise ValueError("roi width and height must be positive")
        return (x, y, width, height)

    @staticmethod
    def _read_float(v, default: float) -> float:
        if v is None:
            return float(default)
        return float(v)

    @staticmethod
    def _read_int(v, default: int) -> int:
        if v is None:
            return int(default)
        return int(v)

    @staticmethod
    def _read_crop_box(v) -> Optional[Tuple[int, int, int, int]]:
        if v is None:
            return None
        if not isinstance(v, list) or len(v) != 4:
            raise ValueError("template_crop must be [x, y, width, height]")
        x, y, width, height = [int(item) for item in v]
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("template_crop values must be non-negative with positive width/height")
        return (x, y, width, height)

    def _load_boards(self, boards_cfg: List[dict]) -> List[BoardProfile]:
        boards: List[BoardProfile] = []
        for board in boards_cfg:
            board_id = str(board.get("board_id", "")).strip()
            board_type = str(board.get("board_type", "")).strip().lower()
            if not board_id:
                raise ValueError("Each board must provide board_id")
            if board_type not in {"checkerboard", "custom_groundmaker"}:
                raise ValueError(f"Unsupported board_type for {board_id}: {board_type}")

            roi = self._parse_roi(board.get("roi"))
            board_size = None
            if board_type == "checkerboard":
                raw_size = board.get("board_size")
                if not isinstance(raw_size, list) or len(raw_size) != 2:
                    raise ValueError(
                        f"checkerboard {board_id} must provide board_size=[cols, rows]"
                    )
                board_size = (int(raw_size[0]), int(raw_size[1]))

            min_points_default = board_size[0] * board_size[1] if board_size else 6
            custom_detector = str(board.get("custom_detector", "feature")).strip().lower()
            if custom_detector not in {"feature", "template_match"}:
                raise ValueError(
                    f"Unsupported custom_detector for {board_id}: {custom_detector}"
                )
            boards.append(
                BoardProfile(
                    board_id=board_id,
                    board_type=board_type,
                    weight=self._read_float(board.get("weight"), 1.0),
                    critical=bool(board.get("critical", True)),
                    roi=roi,
                    board_size=board_size,
                    square_size=self._read_float(board.get("square_size"), 1.0),
                    alpha=self._read_float(board.get("alpha"), 1000.0),
                    beta=self._read_float(board.get("beta"), 0.1),
                    fail_penalty=self._read_float(board.get("fail_penalty"), 1e6),
                    min_detected_points=self._read_int(
                        board.get("min_detected_points"), min_points_default
                    ),
                    degrade_threshold_rmse=self._read_float(
                        board.get("degrade_threshold_rmse"), float("inf")
                    ),
                    degrade_threshold_max_error=self._read_float(
                        board.get("degrade_threshold_max_error"), float("inf")
                    ),
                    degrade_threshold_miss_rate=self._read_float(
                        board.get("degrade_threshold_miss_rate"), float("inf")
                    ),
                    template_image=board.get("template_image"),
                    min_match_count=self._read_int(board.get("min_match_count"), 20),
                    custom_detector=custom_detector,
                    template_match_threshold=self._read_float(
                        board.get("template_match_threshold"), 0.0
                    ),
                    template_binary_threshold=self._read_int(
                        board.get("template_binary_threshold"), 0
                    ),
                    template_crop=self._read_crop_box(board.get("template_crop")),
                )
            )
        return boards

    def _load_custom_templates(self, boards: List[BoardProfile]) -> Dict[str, dict]:
        templates: Dict[str, dict] = {}
        template_cache: Dict[Tuple[str, Optional[Tuple[int, int, int, int]], str], dict] = {}
        for board in boards:
            if not board.template_image:
                continue
            cache_key = (
                str(board.template_image),
                board.template_crop,
                board.custom_detector,
            )
            template_info = template_cache.get(cache_key)
            if template_info is None:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                        with Image.open(str(board.template_image)) as template_image:
                            if (
                                template_image.mode == "P"
                                and "transparency" in template_image.info
                            ):
                                template_image = template_image.convert("RGBA")
                            template_gray = np.ascontiguousarray(
                                np.array(template_image.convert("L"), dtype=np.uint8)
                            )
                except FileNotFoundError:
                    raise FileNotFoundError(
                        f"Cannot read template_image for {board.board_id}: {board.template_image}"
                    )
                except OSError as exc:
                    raise RuntimeError(
                        f"Failed reading template_image for {board.board_id}: {board.template_image}"
                    ) from exc
                if board.template_crop is not None:
                    crop_x, crop_y, crop_w, crop_h = board.template_crop
                    crop_x1 = crop_x + crop_w
                    crop_y1 = crop_y + crop_h
                    if crop_x1 > template_gray.shape[1] or crop_y1 > template_gray.shape[0]:
                        raise ValueError(
                            f"template_crop is outside template image for {board.board_id}: {board.template_crop}"
                        )
                    template_gray = template_gray[crop_y:crop_y1, crop_x:crop_x1]
                if board.custom_detector == "feature":
                    max_dim = max(template_gray.shape[:2])
                    if max_dim > self.template_feature_max_dim:
                        scale = float(self.template_feature_max_dim) / float(max_dim)
                        new_width = max(1, int(round(template_gray.shape[1] * scale)))
                        new_height = max(1, int(round(template_gray.shape[0] * scale)))
                        template_gray = cv2.resize(
                            template_gray,
                            (new_width, new_height),
                            interpolation=cv2.INTER_AREA,
                        )
                h, w = template_gray.shape[:2]
                anchor_points = np.array(
                    [
                        [0.0, 0.0],
                        [w - 1.0, 0.0],
                        [w - 1.0, h - 1.0],
                        [0.0, h - 1.0],
                        [w * 0.5, h * 0.5],
                        [w * 0.25, h * 0.25],
                        [w * 0.75, h * 0.25],
                        [w * 0.75, h * 0.75],
                        [w * 0.25, h * 0.75],
                    ],
                    dtype=np.float32,
                )
                template_info = {
                    "template": template_gray,
                    "anchors": anchor_points,
                }
                if board.custom_detector == "feature":
                    kp, des = self.orb.detectAndCompute(template_gray, None)
                    if des is None or len(kp) < 20:
                        raise RuntimeError(
                            f"Not enough template features for {board.board_id} in {board.template_image}"
                        )
                    template_info["kp"] = kp
                    template_info["des"] = des
                template_cache[cache_key] = template_info
            templates[board.board_id] = template_info
        return templates

    @staticmethod
    def _preprocess_template_match_image(
        gray_image: np.ndarray, board: BoardProfile
    ) -> np.ndarray:
        if board.template_binary_threshold > 0:
            _, processed = cv2.threshold(
                gray_image,
                float(board.template_binary_threshold),
                255,
                cv2.THRESH_BINARY_INV,
            )
            return processed.astype(np.uint8)
        return gray_image

    @staticmethod
    def _points_bbox(points: np.ndarray) -> Tuple[int, int, int, int]:
        reshaped = points.reshape(-1, 2)
        min_x = int(math.floor(float(np.min(reshaped[:, 0]))))
        min_y = int(math.floor(float(np.min(reshaped[:, 1]))))
        max_x = int(math.ceil(float(np.max(reshaped[:, 0]))))
        max_y = int(math.ceil(float(np.max(reshaped[:, 1]))))
        return min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y)

    @staticmethod
    def _expand_bbox(
        bbox: Tuple[int, int, int, int],
        image_shape: Tuple[int, int],
        ratio: float = 0.18,
        min_pad: int = 24,
    ) -> Tuple[int, int, int, int]:
        x, y, width, height = bbox
        img_h, img_w = image_shape[:2]
        pad_x = max(min_pad, int(round(width * ratio)))
        pad_y = max(min_pad, int(round(height * ratio)))
        x0 = max(0, x - pad_x)
        y0 = max(0, y - pad_y)
        x1 = min(img_w, x + width + pad_x)
        y1 = min(img_h, y + height + pad_y)
        return x0, y0, max(1, x1 - x0), max(1, y1 - y0)

    @staticmethod
    def _bbox_iou(
        lhs: Tuple[int, int, int, int], rhs: Tuple[int, int, int, int]
    ) -> float:
        lx, ly, lw, lh = lhs
        rx, ry, rw, rh = rhs
        left = max(lx, rx)
        top = max(ly, ry)
        right = min(lx + lw, rx + rw)
        bottom = min(ly + lh, ry + rh)
        if left >= right or top >= bottom:
            return 0.0
        intersection = float((right - left) * (bottom - top))
        union = float(lw * lh + rw * rh) - intersection
        if union <= 0.0:
            return 0.0
        return intersection / union

    @staticmethod
    def _load_params(param_cfg: Dict[str, dict]) -> List[ParameterSpec]:
        params: List[ParameterSpec] = []
        for name, p in param_cfg.items():
            initial_value = float(p["initial"])
            if "min_offset" in p or "max_offset" in p:
                min_offset = float(p.get("min_offset", 0.0))
                max_offset = float(p.get("max_offset", 0.0))
                min_value = initial_value + min_offset
                max_value = initial_value + max_offset
            else:
                min_value = float(p["min"])
                max_value = float(p["max"])
            if min_value > max_value:
                raise ValueError(
                    f"Parameter {name} has invalid range: min ({min_value}) > max ({max_value})"
                )
            if initial_value < min_value or initial_value > max_value:
                raise ValueError(
                    f"Parameter {name} initial ({initial_value}) is outside range "
                    f"[{min_value}, {max_value}]. Update config min/max to include initial."
                )
            params.append(
                ParameterSpec(
                    name=name,
                    value=initial_value,
                    step=float(p["step"]),
                    min_value=min_value,
                    max_value=max_value,
                    min_step=float(p.get("min_step", 0.001)),
                    decimals=int(p.get("decimals", 4)),
                    field_index=p.get("field_index"),
                    auto_id=p.get("auto_id"),
                    title=p.get("title"),
                    click_x=p.get("click_x"),
                    click_y=p.get("click_y"),
                )
            )
        return params

    @staticmethod
    def _cursor_pos() -> Tuple[int, int]:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return int(pt.x), int(pt.y)

    def _resolve_click_coords(self, spec: ParameterSpec) -> Tuple[int, int]:
        if spec.click_x is None or spec.click_y is None:
            raise ValueError(
                f"Parameter {spec.name} requires click_x and click_y in coordinate mode"
            )

        if self.settings_win is None:
            raise RuntimeError("settings window is not connected")

        rect = self.settings_win.rectangle()
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if spec.click_x < 0 or spec.click_x >= width or spec.click_y < 0 or spec.click_y >= height:
            raise ValueError(
                f"{spec.name} click coordinates out of settings window bounds: "
                f"click=({spec.click_x},{spec.click_y}), window_size=({width},{height})"
            )
        return int(rect.left + spec.click_x), int(rect.top + spec.click_y)

    def _resolve_interaction_point(self, spec: ParameterSpec) -> Tuple[int, int]:
        abs_x, abs_y = self._resolve_click_coords(spec)
        if self.settings_backend != "win32":
            return abs_x, abs_y

        wrapper = None
        try:
            if self.win32_app is not None:
                wrapper = self._resolve_win32_field_wrapper(spec)
        except Exception:
            wrapper = None
        if wrapper is not None:
            try:
                rect = wrapper.rectangle()
                if rect.right > rect.left and rect.bottom > rect.top:
                    return int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2)
            except Exception:
                pass

        try:
            import win32gui  # type: ignore

            hwnd = win32gui.WindowFromPoint((abs_x, abs_y))
            if hwnd:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if right > left and bottom > top:
                    return int((left + right) / 2), int((top + bottom) / 2)
        except Exception:
            pass
        return abs_x, abs_y

    def _resolve_win32_field_wrapper(self, spec: ParameterSpec):
        if self.settings_backend != "win32" or self.win32_app is None:
            return None

        try:
            import win32gui  # type: ignore
        except Exception:
            return None

        hwnd = self._win32_handle_cache.get(spec.name)
        if hwnd and not win32gui.IsWindow(int(hwnd)):
            self._win32_handle_cache.pop(spec.name, None)
            hwnd = None

        if hwnd is None:
            auto_handles = self._auto_locate_camera_parameter_handles()
            fresh_hwnd = int(auto_handles.get(spec.name, 0))

            if not fresh_hwnd:
                if self.forbid_interactive_coordinate_fallback:
                    raise RuntimeError(
                        f"Auto locator could not resolve a win32 handle for {spec.name}; "
                        "interactive coordinate fallback is disabled."
                    )
                abs_x, abs_y = self._resolve_click_coords(spec)
                try:
                    fresh_hwnd = int(win32gui.WindowFromPoint((abs_x, abs_y)))
                except Exception:
                    fresh_hwnd = 0

            if not fresh_hwnd or not win32gui.IsWindow(fresh_hwnd):
                return None

            hwnd = fresh_hwnd
            self._win32_handle_cache[spec.name] = hwnd

        try:
            return self.win32_app.window(handle=int(hwnd))
        except Exception:
            self._win32_handle_cache.pop(spec.name, None)
            return None

    def _log_auto_param_handle_status(self, resolved: Dict[str, int]) -> None:
        if self._auto_param_handle_status_logged:
            return

        camera_param_names = ["roll", "pitch", "yaw", "pos_x", "pos_y", "pos_z"]
        relevant_specs = [spec for spec in self.params if spec.name in camera_param_names]
        if not relevant_specs:
            self._auto_param_handle_status_logged = True
            return

        try:
            import win32gui  # type: ignore
        except Exception:
            self._auto_param_handle_status_logged = True
            return

        print("Auto-locator status for Camera Settings:")
        for name in camera_param_names:
            spec = next((item for item in relevant_specs if item.name == name), None)
            if spec is None:
                continue

            hwnd = int(resolved.get(name, 0) or 0)
            if hwnd and win32gui.IsWindow(hwnd):
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                print(
                    f"  {name}: auto handle={hwnd} rect=({left},{top},{right},{bottom})"
                )
                continue

            has_click = spec.click_x is not None and spec.click_y is not None
            if has_click:
                print(
                    f"  {name}: auto locate failed, fallback to click_x={spec.click_x}, click_y={spec.click_y}"
                )
            else:
                print(f"  {name}: auto locate failed, no click fallback configured")

        self._auto_param_handle_status_logged = True

    def preflight_script_control(self) -> None:
        self.script_control_runtime_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_control_script_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_control_result_path.parent.mkdir(parents=True, exist_ok=True)
        start_point_desc = "n/a"
        if self.script_control_execute_mode != "dde":
            self._connect_script_control_window()
            start_x, start_y = self._resolve_script_control_start_point()
            start_point_desc = f"({start_x},{start_y})"
        print(
            "Script Control preflight: "
            f"title_re={self.script_control_window_title_re}, "
            f"execute_mode={self.script_control_execute_mode}, "
            f"runtime_path={self.script_control_runtime_path}, "
            f"command_path={self.script_control_script_path}, "
            f"result_path={self.script_control_result_path}, "
            f"start_point={start_point_desc}"
        )

    @staticmethod
    def _format_value_map(values: Dict[str, float]) -> str:
        ordered = []
        for name in sorted(values.keys()):
            ordered.append(f"{name}={values[name]:.4f}")
        return ", ".join(ordered)

    def _comparison_mode_explainer(self) -> str:
        if self.comparison_mode == "direct":
            return "direct; real_image is used only for scoring, not as movie background"
        return "overlay_residual; residual is computed against real_image, not rendered into movie"

    def _board_id_summary(self, limit: int = 12) -> str:
        board_ids = [board.board_id for board in self.boards]
        if len(board_ids) <= limit:
            return ", ".join(board_ids)
        visible = ", ".join(board_ids[:limit])
        return f"{visible}, ... ({len(board_ids)} total)"

    @staticmethod
    def _top_board_summary(total_detail: TotalScoreDetail, limit: int = 3) -> str:
        compared = [score for score in total_detail.board_scores if score.compared]
        if not compared:
            return "worst=none"
        compared.sort(key=lambda score: score.total_score, reverse=True)
        leaders = compared[:limit]
        return "worst=" + ", ".join(
            f"{score.board_id}:{score.total_score:.3f}" for score in leaders
        )

    def _print_run_summary(self) -> None:
        print(
            f"Run session: id={self.run_session_id}, started_at={self.run_started_at}"
        )
        print(
            "Run summary: "
            f"output_dir={self.output_dir}, "
            f"real_image={self.cfg['real_image']}, "
            f"comparison_mode={self._comparison_mode_explainer()}"
        )
        print(
            "Boards in score: "
            f"{self._board_id_summary()}"
        )
        print(
            "Start values: "
            f"{self._format_value_map(self._snapshot_values())}"
        )
        if self.resume_result_path is not None:
            score_info = (
                f", resumed_best_score={self.resume_best_score:.6f}"
                if self.resume_best_score is not None
                else ""
            )
            print(f"Resume source: {self.resume_result_path}{score_info}")

    def load_best_values_from_result(self, result_path: Path) -> Optional[Dict[str, float]]:
        if not result_path.exists():
            print(f"Resume skipped: result file not found at {result_path}")
            return None

        with open(result_path, "r", encoding="utf-8") as f:
            result = json.load(f)

        result_mode = str(result.get("comparison_mode", "")).lower().strip()
        if result_mode and result_mode != self.comparison_mode:
            print(
                "Resume skipped: comparison_mode mismatch "
                f"({result_mode} != {self.comparison_mode})"
            )
            return None

        result_board_ids = [
            str(entry.get("board_id"))
            for entry in result.get("boards", [])
            if isinstance(entry, dict) and entry.get("board_id") is not None
        ]
        current_board_ids = [board.board_id for board in self.boards]
        if result_board_ids and result_board_ids != current_board_ids:
            print("Resume skipped: board set mismatch between config and result.json")
            return None

        best_values = result.get("best_values")
        if not isinstance(best_values, dict):
            print(f"Resume skipped: best_values missing in {result_path}")
            return None

        applied: Dict[str, float] = {}
        for param in self.params:
            if param.name not in best_values:
                continue
            param.value = self._quantize_param_value(param, float(best_values[param.name]))
            applied[param.name] = param.value

        if not applied:
            print(f"Resume skipped: no matching parameter values found in {result_path}")
            return None

        self.resume_result_path = result_path
        best_score = result.get("best_score")
        self.resume_best_score = float(best_score) if isinstance(best_score, (int, float)) else None
        print(
            "Resuming from existing best result: "
            f"path={result_path}, values={self._format_value_map(applied)}"
        )
        return applied

    def _connect_script_control_window(self) -> None:
        if self.script_control_win is not None:
            try:
                self.script_control_win.wait("visible enabled", timeout=1)
                return
            except Exception:
                self.script_control_win = None
                self.script_control_app = None
                self._script_control_runtime_loaded = False

        self.script_control_app = Application(backend="win32").connect(
            title_re=self.script_control_window_title_re,
            timeout=10,
        )
        self.script_control_win = self.script_control_app.window(
            title_re=self.script_control_window_title_re
        )
        self.script_control_win.wait("visible enabled", timeout=10)

    def _get_script_control_side_buttons(self) -> Dict[str, Tuple[int, int, int, int]]:
        if self.script_control_win is None:
            raise RuntimeError("Script Control window is not connected")

        rect = self.script_control_win.rectangle()
        small_buttons: List[Tuple[int, Tuple[int, int, int, int]]] = []
        for ctrl in self.script_control_win.descendants():
            try:
                if getattr(ctrl.element_info, "class_name", "") != "Button":
                    continue
                button_rect = ctrl.rectangle()
                width = int(button_rect.right - button_rect.left)
                height = int(button_rect.bottom - button_rect.top)
                if button_rect.left <= rect.right - 140:
                    continue
                if 68 <= width <= 75 and 20 <= height <= 26:
                    small_buttons.append(
                        (
                            int(button_rect.top),
                            (
                                int(button_rect.left),
                                int(button_rect.top),
                                int(button_rect.right),
                                int(button_rect.bottom),
                            ),
                        )
                    )
            except Exception:
                continue

        small_buttons.sort(key=lambda item: item[0])
        if len(small_buttons) < 5:
            raise RuntimeError(
                f"Unable to resolve Script Control side buttons; found {len(small_buttons)} small buttons"
            )

        return {
            "close": small_buttons[0][1],
            "new": small_buttons[1][1],
            "open": small_buttons[2][1],
            "edit": small_buttons[3][1],
            "clear": small_buttons[4][1],
        }

    def _get_script_control_side_button_handles(self) -> Dict[str, int]:
        if self.script_control_win is None:
            raise RuntimeError("Script Control window is not connected")

        rect = self.script_control_win.rectangle()
        small_buttons: List[Tuple[int, int]] = []
        for ctrl in self.script_control_win.descendants():
            try:
                if getattr(ctrl.element_info, "class_name", "") != "Button":
                    continue
                button_rect = ctrl.rectangle()
                width = int(button_rect.right - button_rect.left)
                height = int(button_rect.bottom - button_rect.top)
                hwnd = int(getattr(ctrl, "handle", 0))
                if not hwnd:
                    continue
                if button_rect.left <= rect.right - 140:
                    continue
                if 68 <= width <= 75 and 20 <= height <= 26:
                    small_buttons.append((int(button_rect.top), hwnd))
            except Exception:
                continue

        small_buttons.sort(key=lambda item: item[0])
        if len(small_buttons) < 5:
            raise RuntimeError(
                f"Unable to resolve Script Control side button handles; found {len(small_buttons)} small buttons"
            )

        return {
            "close": small_buttons[0][1],
            "new": small_buttons[1][1],
            "open": small_buttons[2][1],
            "edit": small_buttons[3][1],
            "clear": small_buttons[4][1],
        }

    @staticmethod
    def _rect_center(rect: Tuple[int, int, int, int]) -> Tuple[int, int]:
        left, top, right, bottom = rect
        return int((left + right) / 2), int((top + bottom) / 2)

    def _click_screen_rect(self, rect: Tuple[int, int, int, int]) -> None:
        original_cursor = self._cursor_pos()
        try:
            mouse.click(button="left", coords=self._rect_center(rect))
        finally:
            try:
                mouse.move(coords=original_cursor)
            except Exception:
                pass

    def _click_button_handle(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        self._send_window_message(hwnd, BM_CLICK, 0, 0)
        return True

    def _click_script_control_button(
        self,
        hwnd: int,
        rect: Tuple[int, int, int, int],
        *,
        description: str,
    ) -> bool:
        if self._click_button_handle(hwnd):
            return True
        if not self.script_control_allow_physical_fallback:
            raise RuntimeError(
                f"Script Control {description} requires a physical click, but "
                "script_control_allow_physical_fallback is disabled"
            )
        self._click_screen_rect(rect)
        return False

    @staticmethod
    def _is_inactive_desktop_click_error(exc: Exception) -> bool:
        text = str(exc)
        return "There is no active desktop" in text or "SetCursorPos" in text

    def _resolve_script_control_start_button_handle(self) -> Optional[int]:
        if self.script_control_win is None:
            return None

        rect = self.script_control_win.rectangle()
        preferred_right_margin = 50
        preferred_bottom_margin = 75
        candidates: List[Tuple[float, int]] = []
        try:
            for ctrl in self.script_control_win.descendants():
                if getattr(ctrl.element_info, "class_name", "") != "Button":
                    continue
                button_rect = ctrl.rectangle()
                width = int(button_rect.right - button_rect.left)
                height = int(button_rect.bottom - button_rect.top)
                if width < 60 or height < 30:
                    continue
                center_x = int((button_rect.left + button_rect.right) / 2)
                center_y = int((button_rect.top + button_rect.bottom) / 2)
                right_margin = int(rect.right - center_x)
                bottom_margin = int(rect.bottom - center_y)
                if right_margin < 0 or right_margin > 120 or bottom_margin < 0 or bottom_margin > 160:
                    continue
                hwnd = int(getattr(ctrl, "handle", 0))
                if not hwnd:
                    continue
                score = abs(right_margin - preferred_right_margin) + abs(
                    bottom_margin - preferred_bottom_margin
                )
                candidates.append((float(score), hwnd))
        except Exception:
            return None

        if not candidates:
            return None
        _, hwnd = min(candidates, key=lambda item: item[0])
        return hwnd

    def _trigger_script_control_start(
        self,
        start_x: int,
        start_y: int,
        prefer_background: bool = True,
        allow_physical_fallback: bool = True,
    ) -> bool:
        if prefer_background:
            hwnd = self._resolve_script_control_start_button_handle()
            if hwnd:
                try:
                    self._send_window_message(hwnd, BM_CLICK, 0, 0)
                    return False
                except Exception:
                    pass
            if not allow_physical_fallback:
                raise RuntimeError("Script Control background Start trigger did not execute")

        if not allow_physical_fallback:
            raise RuntimeError("Script Control physical Start fallback is disabled")

        click_deadline = time.time() + max(5.0, self.script_control_timeout_sec * 6.0)
        while True:
            original_cursor = self._cursor_pos()
            try:
                mouse.click(button="left", coords=(start_x, start_y))
                return False
            except Exception as exc:
                if self._is_inactive_desktop_click_error(exc):
                    if self.script_control_manual_start_timeout_sec > 0:
                        print(
                            "Script Control Start requires manual click; waiting up to "
                            f"{self.script_control_manual_start_timeout_sec:.1f}s for user Start"
                        )
                        return True
                    if time.time() < click_deadline:
                        time.sleep(1.0)
                        continue
                if time.time() >= click_deadline or not self._is_inactive_desktop_click_error(exc):
                    raise
            finally:
                try:
                    mouse.move(coords=original_cursor)
                except Exception:
                    pass

    def _get_script_control_browser_window(self):
        try:
            browser = Desktop(backend="win32").window(title_re="CarMaker Office - Browser")
            browser.wait("visible enabled", timeout=0.5)
            return browser
        except Exception:
            return None

    def _get_script_control_warning_window(self):
        try:
            warning = Desktop(backend="win32").window(title_re="CarMaker Office - Warning")
            warning.wait("visible enabled", timeout=0.5)
            return warning
        except Exception:
            return None

    def _dismiss_script_control_popups(self) -> None:
        warning = self._get_script_control_warning_window()
        if warning is not None:
            rightmost_handle = 0
            rightmost = None
            for ctrl in warning.descendants():
                try:
                    if getattr(ctrl.element_info, "class_name", "") != "Button":
                        continue
                    rect = ctrl.rectangle()
                    width = int(rect.right - rect.left)
                    height = int(rect.bottom - rect.top)
                    if 45 <= width <= 80 and 20 <= height <= 30:
                        candidate = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
                        if rightmost is None or candidate[0] > rightmost[0]:
                            rightmost = candidate
                            rightmost_handle = int(getattr(ctrl, "handle", 0))
                except Exception:
                    continue
            if rightmost is not None:
                self._click_script_control_button(
                    rightmost_handle,
                    rightmost,
                    description="warning confirmation",
                )
                time.sleep(0.2)

        browser = self._get_script_control_browser_window()
        if browser is not None:
            browser_rect = browser.rectangle()
            cancel_handle = 0
            cancel_rect = None
            for ctrl in browser.descendants():
                try:
                    if getattr(ctrl.element_info, "class_name", "") != "Button":
                        continue
                    rect = ctrl.rectangle()
                    width = int(rect.right - rect.left)
                    height = int(rect.bottom - rect.top)
                    if (
                        70 <= width <= 80
                        and 20 <= height <= 24
                        and rect.left > browser_rect.right - 110
                        and rect.top < browser_rect.top + 220
                    ):
                        candidate = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
                        if cancel_rect is None or candidate[1] > cancel_rect[1]:
                            cancel_rect = candidate
                            cancel_handle = int(getattr(ctrl, "handle", 0))
                except Exception:
                    continue
            if cancel_rect is not None:
                self._click_script_control_button(
                    cancel_handle,
                    cancel_rect,
                    description="browser cancel",
                )
                time.sleep(0.2)

    def _resolve_script_control_browser_path_rect(self, browser) -> Tuple[int, int, int, int]:
        browser_rect = browser.rectangle()
        candidate_rect = None
        for ctrl in browser.descendants():
            try:
                if getattr(ctrl.element_info, "class_name", "") != "TkChild":
                    continue
                rect = ctrl.rectangle()
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if width > 300 and 18 <= height <= 30 and rect.top < browser_rect.top + 130:
                    candidate_rect = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
            except Exception:
                continue
        if candidate_rect is None:
            raise RuntimeError("Unable to resolve Script Control Browser path entry")
        return candidate_rect

    def _resolve_script_control_browser_path_handle(self, browser) -> int:
        browser_rect = browser.rectangle()
        candidate_handle = 0
        for ctrl in browser.descendants():
            try:
                if getattr(ctrl.element_info, "class_name", "") != "TkChild":
                    continue
                rect = ctrl.rectangle()
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if width > 300 and 18 <= height <= 30 and rect.top < browser_rect.top + 130:
                    candidate_handle = int(getattr(ctrl, "handle", 0))
            except Exception:
                continue
        if not candidate_handle:
            raise RuntimeError("Unable to resolve Script Control Browser path entry handle")
        return candidate_handle

    def _resolve_script_control_browser_ok_rect(self, browser) -> Tuple[int, int, int, int]:
        browser_rect = browser.rectangle()
        ok_rect = None
        for ctrl in browser.descendants():
            try:
                if getattr(ctrl.element_info, "class_name", "") != "Button":
                    continue
                rect = ctrl.rectangle()
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if (
                    70 <= width <= 80
                    and 20 <= height <= 24
                    and rect.left > browser_rect.right - 110
                    and rect.top < browser_rect.top + 170
                ):
                    candidate = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
                    if ok_rect is None or candidate[1] < ok_rect[1]:
                        ok_rect = candidate
            except Exception:
                continue
        if ok_rect is None:
            raise RuntimeError("Unable to resolve Script Control Browser OK button")
        return ok_rect

    def _resolve_script_control_browser_ok_handle(self, browser) -> int:
        browser_rect = browser.rectangle()
        ok_handle = 0
        best_top = None
        for ctrl in browser.descendants():
            try:
                if getattr(ctrl.element_info, "class_name", "") != "Button":
                    continue
                rect = ctrl.rectangle()
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                hwnd = int(getattr(ctrl, "handle", 0))
                if not hwnd:
                    continue
                if (
                    70 <= width <= 80
                    and 20 <= height <= 24
                    and rect.left > browser_rect.right - 110
                    and rect.top < browser_rect.top + 170
                ):
                    if best_top is None or int(rect.top) < best_top:
                        best_top = int(rect.top)
                        ok_handle = hwnd
            except Exception:
                continue
        if not ok_handle:
            raise RuntimeError("Unable to resolve Script Control Browser OK button handle")
        return ok_handle

    def _ensure_script_control_script_loaded(self) -> None:
        self._connect_script_control_window()
        if self._script_control_runtime_loaded:
            return

        self.script_control_runtime_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_script_control_runtime_wrapper()
        self._dismiss_script_control_popups()

        browser = None
        for _ in range(3):
            side_buttons = self._get_script_control_side_buttons()
            side_button_handles = self._get_script_control_side_button_handles()
            clicked_in_background = self._click_script_control_button(
                side_button_handles.get("open", 0),
                side_buttons["open"],
                description="Browser Open",
            )

            browser_deadline = time.time() + self.script_control_timeout_sec
            while time.time() < browser_deadline:
                browser = self._get_script_control_browser_window()
                if browser is not None:
                    break
                time.sleep(0.1)
            if (
                browser is None
                and clicked_in_background
                and self.script_control_allow_physical_fallback
            ):
                self._click_screen_rect(side_buttons["open"])
                browser_deadline = time.time() + self.script_control_timeout_sec
                while time.time() < browser_deadline:
                    browser = self._get_script_control_browser_window()
                    if browser is not None:
                        break
                    time.sleep(0.1)
            if browser is not None:
                break
            self._dismiss_script_control_popups()
            time.sleep(0.2)

        if browser is None:
            raise RuntimeError("Timed out waiting for Script Control Browser window")

        entry_handle = self._resolve_script_control_browser_path_handle(browser)
        ok_rect = self._resolve_script_control_browser_ok_rect(browser)
        ok_handle = self._resolve_script_control_browser_ok_handle(browser)
        self._send_window_message(entry_handle, 0x000C, 0, self.script_control_browser_path)
        clicked_ok_in_background = self._click_script_control_button(
            ok_handle,
            ok_rect,
            description="Browser OK",
        )

        loaded_deadline = time.time() + self.script_control_timeout_sec
        ok_fallback_attempted = not clicked_ok_in_background
        while time.time() < loaded_deadline:
            warning = self._get_script_control_warning_window()
            browser = self._get_script_control_browser_window()
            if (
                browser is not None
                and warning is None
                and clicked_ok_in_background
                and not ok_fallback_attempted
                and self.script_control_allow_physical_fallback
            ):
                self._click_screen_rect(ok_rect)
                ok_fallback_attempted = True
                time.sleep(0.2)
                warning = self._get_script_control_warning_window()
                browser = self._get_script_control_browser_window()
            if warning is not None:
                leftmost_handle = 0
                leftmost = None
                for ctrl in warning.descendants():
                    try:
                        if getattr(ctrl.element_info, "class_name", "") != "Button":
                            continue
                        rect = ctrl.rectangle()
                        width = int(rect.right - rect.left)
                        height = int(rect.bottom - rect.top)
                        if 45 <= width <= 80 and 20 <= height <= 30:
                            candidate = (
                                int(rect.left),
                                int(rect.top),
                                int(rect.right),
                                int(rect.bottom),
                            )
                            if leftmost is None or candidate[0] < leftmost[0]:
                                leftmost = candidate
                                leftmost_handle = int(getattr(ctrl, "handle", 0))
                    except Exception:
                        continue
                if leftmost is not None:
                    self._click_script_control_button(
                        leftmost_handle,
                        leftmost,
                        description="runtime load confirmation",
                    )
                    time.sleep(0.2)

            warning = self._get_script_control_warning_window()
            if browser is None and warning is None:
                self._script_control_runtime_loaded = True
                return
            time.sleep(0.1)

        raise RuntimeError(
            "Timed out loading the runtime Tcl into Script Control via Browser"
        )

    def _write_script_control_runtime_wrapper(self) -> None:
        command_path = self.script_control_script_path.as_posix()
        result_path = self.script_control_result_path.as_posix()
        wrapper_lines = [
            f'set __copilot_command_script "{command_path}"',
            f'set __copilot_result_path "{result_path}"',
            'set __copilot_rc [catch {uplevel #0 [list RunScript $__copilot_command_script]} __copilot_msg]',
            'if {$__copilot_rc != 0} {',
            '    set out [open $__copilot_result_path w]',
            '    puts $out "rc=$__copilot_rc"',
            '    puts $out "msg_begin"',
            '    puts $out $__copilot_msg',
            '    puts $out "msg_end"',
            '    close $out',
            '}',
        ]
        self.script_control_runtime_path.write_text(
            "\n".join(wrapper_lines) + "\n",
            encoding="utf-8",
        )

    def _run_script_control_console_command(self, command: str) -> None:
        if self.script_control_win is None:
            raise RuntimeError("Script Control window is not connected")

        rect = self.script_control_win.rectangle()
        click_x = int(rect.left + self.script_control_console_click_x)
        click_y = int(rect.bottom - self.script_control_console_click_y_from_bottom)
        self._set_clipboard_text(command)
        self._click_screen_rect((click_x - 1, click_y - 1, click_x + 1, click_y + 1))
        time.sleep(0.1)
        send_keys("^v", pause=0.01)
        send_keys("{ENTER}", pause=0.01)

    def _run_script_control_dde_runscript(self, script_path: Path) -> bool:
        if not self.script_control_prefer_dde:
            return False

        try:
            import win32ui  # noqa: F401
            import dde  # type: ignore
        except Exception:
            return False

        server = None
        try:
            server = dde.CreateServer()
            server.Create(f"CopilotScriptControl.{uuid.uuid4().hex}")
            conv = dde.CreateConversation(server)
            conv.ConnectTo(self.script_control_dde_service, self.script_control_dde_topic)
            conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
            return True
        except Exception as exc:
            print(
                "Script Control DDE RunScript failed: "
                f"service={self.script_control_dde_service}, "
                f"topic={self.script_control_dde_topic}, error={exc}"
            )
            return False
        finally:
            if server is not None:
                try:
                    server.Shutdown()
                except Exception:
                    pass

    def _resolve_script_control_start_point(self) -> Tuple[int, int]:
        if self.script_control_win is None:
            raise RuntimeError("Script Control window is not connected")

        rect = self.script_control_win.rectangle()
        preferred_right_margin = 50
        preferred_bottom_margin = 75
        rel_x = int(self.script_control_start_click_x)
        rel_y = int(self.script_control_start_click_y)
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        resolved_x = rel_x
        resolved_y = rel_y
        fallback_used = False

        if rel_x < 0 or rel_x >= width:
            right_margin = self.script_control_start_click_x_from_right
            if right_margin is None:
                right_margin = preferred_right_margin
            resolved_x = max(0, min(width - 1, width - int(right_margin)))
            fallback_used = True

        if rel_y < 0 or rel_y >= height:
            bottom_margin = self.script_control_start_click_y_from_bottom
            if bottom_margin is None:
                bottom_margin = preferred_bottom_margin
            resolved_y = max(0, min(height - 1, height - int(bottom_margin)))
            fallback_used = True

        if fallback_used and 0 <= resolved_x < width and 0 <= resolved_y < height:
            print(
                "Script Control start click fallback: "
                f"configured=({rel_x},{rel_y}), resolved=({resolved_x},{resolved_y}), "
                f"window_size=({width},{height})"
            )
            return int(rect.left + resolved_x), int(rect.top + resolved_y)

        if 0 <= resolved_x < width and 0 <= resolved_y < height:
            return int(rect.left + resolved_x), int(rect.top + resolved_y)

        candidates: List[Tuple[float, int, int]] = []
        try:
            for ctrl in self.script_control_win.descendants():
                if getattr(ctrl.element_info, "class_name", "") != "Button":
                    continue
                button_rect = ctrl.rectangle()
                button_width = int(button_rect.right - button_rect.left)
                button_height = int(button_rect.bottom - button_rect.top)
                if button_width < 60 or button_height < 30:
                    continue
                center_x = int((button_rect.left + button_rect.right) / 2)
                center_y = int((button_rect.top + button_rect.bottom) / 2)
                right_margin = int(rect.right - center_x)
                bottom_margin = int(rect.bottom - center_y)
                if right_margin < 0 or right_margin > 120 or bottom_margin < 0 or bottom_margin > 160:
                    continue
                score = abs(right_margin - preferred_right_margin) + abs(
                    bottom_margin - preferred_bottom_margin
                )
                candidates.append((float(score), center_x, center_y))
        except Exception:
            candidates = []

        if candidates:
            _, center_x, center_y = min(candidates, key=lambda item: item[0])
            print(
                "Script Control start click auto-located: "
                f"configured=({rel_x},{rel_y}), resolved=({center_x - rect.left},{center_y - rect.top}), "
                f"window_size=({width},{height})"
            )
            return center_x, center_y

        raise ValueError(
            "Script Control start click is outside the window bounds: "
            f"click=({rel_x},{rel_y}), resolved=({resolved_x},{resolved_y}), "
            f"window_size=({width},{height})"
        )

    def _render_script_control_apply_script(self, params: List[ParameterSpec]) -> str:
        unsupported = [p.name for p in params if p.name not in self.SCRIPT_CONTROL_WRITE_WIDGETS]
        if unsupported:
            joined = ", ".join(unsupported)
            raise RuntimeError(f"script_control mode does not support parameters: {joined}")

        result_path = self.script_control_result_path.as_posix()
        lines = [
            f'set out [open "{result_path}" w]',
            'proc emit {text} {',
            '    global out',
            '    puts $out $text',
            '}',
            'set rc [catch {send IPG-MOVIE {',
            '    if {![winfo exists .camera]} {error "missing widget .camera"}',
        ]

        for param in params:
            widget = self.SCRIPT_CONTROL_WRITE_WIDGETS[param.name]
            value_text = f"{self._quantize_param_value(param, param.value):.{param.decimals}f}"
            lines.extend(
                [
                    f'    if {{![winfo exists {widget}]}} {{error "missing widget {widget}"}}',
                    f'    {widget} delete 0 end',
                    f'    {widget} insert 0 {value_text}',
                ]
            )

        lines.extend(
            [
                '    update idletasks',
                '    if {![winfo exists .camera.btn.set]} {error "missing widget .camera.btn.set"}',
                '    .camera.btn.set invoke',
                '    update idletasks',
                '    set result {}',
            ]
        )

        for param in params:
            read_widget = self.SCRIPT_CONTROL_READ_WIDGETS[param.name]
            lines.extend(
                [
                    f'    if {{![winfo exists {read_widget}]}} {{error "missing widget {read_widget}"}}',
                    f'    lappend result "{param.name}=[{read_widget} get]"',
                ]
            )

        lines.extend(
            [
                '    join $result "\\n"',
                '}} msg]',
                'emit "rc=$rc"',
                'emit "msg_begin"',
                'emit $msg',
                'emit "msg_end"',
                'close $out',
            ]
        )
        return "\n".join(lines) + "\n"

    def _render_script_control_read_script(self, params: List[ParameterSpec]) -> str:
        unsupported = [p.name for p in params if p.name not in self.SCRIPT_CONTROL_WRITE_WIDGETS]
        if unsupported:
            joined = ", ".join(unsupported)
            raise RuntimeError(f"script_control mode does not support parameters: {joined}")

        result_path = self.script_control_result_path.as_posix()
        lines = [
            f'set out [open "{result_path}" w]',
            'proc emit {text} {',
            '    global out',
            '    puts $out $text',
            '}',
            'set rc [catch {send IPG-MOVIE {',
            '    if {![winfo exists .camera]} {error "missing widget .camera"}',
            '    set result {}',
        ]

        for param in params:
            widget = self.SCRIPT_CONTROL_WRITE_WIDGETS[param.name]
            lines.extend(
                [
                    f'    if {{![winfo exists {widget}]}} {{error "missing widget {widget}"}}',
                    f'    lappend result "{param.name}=[{widget} get]"',
                ]
            )

        lines.extend(
            [
                '    join $result "\\n"',
                '}} msg]',
                'emit "rc=$rc"',
                'emit "msg_begin"',
                'emit $msg',
                'emit "msg_end"',
                'close $out',
            ]
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _parse_script_control_result_text(text: str) -> Tuple[int, str]:
        lines = [line.rstrip("\r") for line in text.splitlines()]
        rc_line = next((line for line in lines if line.startswith("rc=")), None)
        if rc_line is None:
            raise RuntimeError(f"Script Control result is missing rc=: {text!r}")

        try:
            rc = int(rc_line.split("=", 1)[1].strip())
        except ValueError as exc:
            raise RuntimeError(f"Invalid Script Control rc line: {rc_line!r}") from exc

        if "msg_begin" in lines and "msg_end" in lines:
            start = lines.index("msg_begin") + 1
            end = lines.index("msg_end")
            msg = "\n".join(lines[start:end]).strip()
        else:
            msg = "\n".join(line for line in lines if not line.startswith("rc=")).strip()
        return rc, msg

    @staticmethod
    def _is_script_control_result_complete(text: str) -> bool:
        stripped = text.strip()
        if not stripped or "rc=" not in stripped:
            return False
        if "msg_begin" in stripped:
            return "msg_end" in stripped
        return True

    def _run_script_control_script(self, script_text: str) -> str:
        if self.script_control_execute_mode != "dde":
            self._connect_script_control_window()
        self.script_control_script_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_control_result_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_control_script_path.write_text(script_text, encoding="utf-8")
        try:
            self.script_control_result_path.unlink()
        except FileNotFoundError:
            pass

        for attempt in range(3):
            manual_start_required = False
            if self.script_control_execute_mode == "console":
                self._run_script_control_console_command(
                    f"RunScript {self.script_control_script_path.as_posix()}"
                )
            elif self.script_control_execute_mode == "dde":
                if not self._run_script_control_dde_runscript(self.script_control_script_path):
                    raise RuntimeError(
                        "Script Control DDE RunScript did not execute"
                    )
            else:
                if not self._run_script_control_dde_runscript(self.script_control_script_path):
                    start_x, start_y = self._resolve_script_control_start_point()
                    if attempt > 0 or self._script_control_runtime_loaded:
                        self._ensure_script_control_script_loaded()
                    manual_start_required = self._trigger_script_control_start(
                        start_x,
                        start_y,
                        prefer_background=self.script_control_prefer_background_start,
                        allow_physical_fallback=self.script_control_allow_physical_fallback,
                    )
            deadline = time.time() + self.script_control_timeout_sec
            if manual_start_required:
                deadline += self.script_control_manual_start_timeout_sec
            while time.time() < deadline:
                if self.script_control_result_path.exists():
                    text = self.script_control_result_path.read_text(encoding="utf-8", errors="replace")
                    if self._is_script_control_result_complete(text):
                        rc, msg = self._parse_script_control_result_text(text)
                        if rc != 0:
                            raise RuntimeError(f"Script Control apply failed: {msg}")
                        self._script_control_runtime_loaded = True
                        return msg
                time.sleep(0.1)

        raise RuntimeError(
            "Timed out waiting for Script Control result file. "
            f"Script Control did not execute the runtime script {self.script_control_runtime_path}."
        )

    def _reset_script_control_runtime_state(self) -> None:
        self.script_control_win = None
        self.script_control_app = None
        self._script_control_runtime_loaded = False

    def _recover_after_runtime_error(self, expected_values: Dict[str, float]) -> bool:
        for param in self.params:
            if param.name in expected_values:
                param.value = self._quantize_param_value(param, float(expected_values[param.name]))

        self._reset_script_control_runtime_state()
        try:
            self._apply_value_map(expected_values)
            return True
        except RuntimeError:
            self._reset_script_control_runtime_state()
            return False

    def _apply_value_map_or_recover(self, values: Dict[str, float], context: str) -> None:
        try:
            self._apply_value_map(values)
        except RuntimeError as exc:
            restored = self._recover_after_runtime_error(values)
            if restored:
                return
            raise RuntimeError(f"{context}: {exc}") from exc

    def _read_script_control_values(self, params: List[ParameterSpec]) -> Dict[str, float]:
        msg = self._run_script_control_script(self._render_script_control_read_script(params))
        captured: Dict[str, float] = {}
        for line in msg.splitlines():
            if "=" not in line:
                continue
            name, raw_value = line.split("=", 1)
            try:
                captured[name.strip()] = float(raw_value.replace(",", ".").strip())
            except ValueError:
                continue
        return captured

    def _apply_script_control_params(self, params: List[ParameterSpec]) -> None:
        if not params:
            return

        for param in params:
            param.value = self._quantize_param_value(param, param.value)

        last_error: Optional[RuntimeError] = None
        for attempt in range(3):
            msg = self._run_script_control_script(self._render_script_control_apply_script(params))
            observed: Dict[str, float] = {}
            for line in msg.splitlines():
                if "=" not in line:
                    continue
                name, raw_value = line.split("=", 1)
                try:
                    observed[name.strip()] = float(raw_value.replace(",", ".").strip())
                except ValueError:
                    continue

            mismatches: List[str] = []
            for param in params:
                expected = self._quantize_param_value(param, param.value)
                actual = observed.get(param.name)
                read_decimals = self.SCRIPT_CONTROL_READ_DECIMALS.get(param.name, param.decimals)
                expected_readback = self._quantize_value(expected, read_decimals)
                tolerance = max((10 ** (-read_decimals)) * 0.5, 1e-6)
                if actual is None or not math.isclose(
                    actual,
                    expected_readback,
                    rel_tol=0.0,
                    abs_tol=tolerance,
                ):
                    mismatches.append(
                        f"{param.name}: expected {expected_readback}, read back {actual}"
                    )

            if not mismatches:
                time.sleep(self.script_control_settle_sec)
                return

            last_error = RuntimeError(
                "Script Control verification failed after apply attempt "
                f"{attempt + 1}: " + "; ".join(mismatches)
            )
            time.sleep(0.1)

        if last_error is not None:
            raise last_error

        time.sleep(self.script_control_settle_sec)

    def _auto_locate_camera_parameter_handles(self) -> Dict[str, int]:
        if self._auto_param_handle_cache is not None:
            return dict(self._auto_param_handle_cache)

        if self.settings_backend != "win32" or self.settings_win is None:
            self._auto_param_handle_cache = {}
            return {}

        camera_param_names = {"roll", "pitch", "yaw", "pos_x", "pos_y", "pos_z"}
        if not any(spec.name in camera_param_names for spec in self.params):
            self._auto_param_handle_cache = {}
            return {}

        try:
            import win32gui  # type: ignore
        except Exception:
            self._auto_param_handle_cache = {}
            return {}

        root_handle = int(getattr(self.settings_win, "handle", 0) or 0)
        if not root_handle:
            self._auto_param_handle_cache = {}
            return {}

        handles: List[int] = []
        win32gui.EnumChildWindows(root_handle, lambda h, acc: acc.append(int(h)), handles)

        candidates = []
        for hwnd in handles:
            if not win32gui.IsWindowVisible(hwnd):
                continue
            if win32gui.GetClassName(hwnd) != "TkChild":
                continue

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = int(right - left)
            height = int(bottom - top)
            if not (40 <= width <= 60 and 18 <= height <= 21):
                continue

            candidates.append(
                {
                    "handle": int(hwnd),
                    "left": int(left),
                    "top": int(top),
                    "right": int(right),
                    "bottom": int(bottom),
                    "parent": int(win32gui.GetParent(hwnd) or 0),
                }
            )

        if not candidates:
            self._auto_param_handle_cache = {}
            self._log_auto_param_handle_status({})
            return {}

        parent_counts: Dict[int, int] = {}
        for candidate in candidates:
            parent_counts[candidate["parent"]] = parent_counts.get(candidate["parent"], 0) + 1
        dominant_parent = max(parent_counts.items(), key=lambda item: item[1])[0]
        candidates = [candidate for candidate in candidates if candidate["parent"] == dominant_parent]

        row_clusters: Dict[int, List[dict]] = {}
        for candidate in candidates:
            matched_row = None
            for row_top in row_clusters:
                if abs(candidate["top"] - row_top) <= 2:
                    matched_row = row_top
                    break
            if matched_row is None:
                row_clusters[candidate["top"]] = [candidate]
            else:
                row_clusters[matched_row].append(candidate)

        horizontal_row: List[dict] = []
        for row_top in sorted(row_clusters):
            row_items = sorted(row_clusters[row_top], key=lambda candidate: candidate["left"])
            if len(row_items) >= 3:
                horizontal_row = row_items[:3]
                break

        vertical_column: List[dict] = []
        if horizontal_row:
            base_left = horizontal_row[0]["left"]
            base_top = horizontal_row[0]["top"]
            vertical_column = [
                candidate
                for candidate in candidates
                if abs(candidate["left"] - base_left) <= 2 and candidate["top"] > base_top + 40
            ]
            vertical_column.sort(key=lambda candidate: candidate["top"])

        resolved: Dict[str, int] = {}
        if len(horizontal_row) >= 3:
            resolved["roll"] = horizontal_row[0]["handle"]
            resolved["pitch"] = horizontal_row[1]["handle"]
            resolved["yaw"] = horizontal_row[2]["handle"]
        if len(vertical_column) >= 3:
            resolved["pos_x"] = vertical_column[0]["handle"]
            resolved["pos_y"] = vertical_column[1]["handle"]
            resolved["pos_z"] = vertical_column[2]["handle"]

        self._auto_param_handle_cache = dict(resolved)
        self._log_auto_param_handle_status(resolved)
        return resolved

    def _resolve_coordinate_interaction(self, spec: ParameterSpec):
        wrapper = None
        if self.coordinate_input_mode != "mouse":
            wrapper = self._resolve_win32_field_wrapper(spec)

        mode = self.coordinate_input_mode
        if mode == "auto":
            mode = "mouse"

        if mode == "mouse" and self.forbid_interactive_coordinate_fallback:
            raise RuntimeError(
                f"Interactive coordinate fallback is disabled and no non-interactive handle was resolved for {spec.name}."
            )

        if mode in {"message", "focus"}:
            if wrapper is None:
                raise RuntimeError(
                    f"coordinate_input_mode={mode} requires a win32 field handle for {spec.name}"
                )
            return mode, wrapper, None

        abs_x, abs_y = self._resolve_interaction_point(spec)
        return mode, None, (abs_x, abs_y)

    @staticmethod
    def _send_window_message(hwnd: int, msg: int, wparam=0, lparam=0):
        user32 = ctypes.windll.user32
        return user32.SendMessageW(int(hwnd), msg, wparam, lparam)

    def _read_window_text(self, wrapper) -> str:
        hwnd = int(getattr(wrapper, "handle", 0))
        if not hwnd:
            raise RuntimeError("Cannot read control text without a valid window handle")

        length = int(self._send_window_message(hwnd, 0x000E, 0, 0))
        buffer = ctypes.create_unicode_buffer(max(32, length + 2))
        self._send_window_message(
            hwnd,
            0x000D,
            len(buffer),
            ctypes.cast(buffer, ctypes.c_wchar_p),
        )
        return buffer.value.strip()

    def _write_window_text(self, wrapper, text: str) -> None:
        hwnd = int(getattr(wrapper, "handle", 0))
        if not hwnd:
            raise RuntimeError("Cannot write control text without a valid window handle")

        try:
            import win32con  # type: ignore
        except Exception as exc:
            raise RuntimeError("win32con is required for coordinate_input_mode=message") from exc

        self._send_window_message(hwnd, 0x000C, 0, text)
        self._send_window_message(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
        self._send_window_message(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)

    @staticmethod
    def _focus_handle(hwnd: int) -> bool:
        try:
            user32 = ctypes.windll.user32
            user32.SetForegroundWindow(int(hwnd))
            user32.SetFocus(int(hwnd))
            return True
        except Exception:
            return False

    def _activate_coordinate_field(self, interaction_mode: str, wrapper, point) -> None:
        if interaction_mode == "message":
            return

        if interaction_mode == "focus":
            settings_handle = getattr(self.settings_win, "handle", 0)
            wrapper_handle = getattr(wrapper, "handle", 0)
            if settings_handle:
                self._focus_handle(int(settings_handle))
            if wrapper_handle and self._focus_handle(int(wrapper_handle)):
                return
            self._safe_focus(self.settings_win)
            self._safe_focus(wrapper)
            return

        self._safe_focus(self.settings_win)
        abs_x, abs_y = point
        mouse.click(button="left", coords=(abs_x, abs_y))
        time.sleep(0.02)
        mouse.double_click(button="left", coords=(abs_x, abs_y))

    def _set_coordinate_value(self, spec: ParameterSpec, value: float, decimals: int) -> None:
        value = self._quantize_param_value(spec, value)
        text = f"{value:.{decimals}f}"
        unit = 10 ** (-decimals)
        tolerance = max(unit * 0.5, 1e-6)
        last_read = None
        interaction_mode, wrapper, point = self._resolve_coordinate_interaction(spec)

        for _ in range(3):
            try:
                if interaction_mode == "message":
                    self._write_window_text(wrapper, text)
                else:
                    self._activate_coordinate_field(interaction_mode, wrapper, point)
                    send_keys("^a{BACKSPACE}", pause=0.01)
                    send_keys(text, with_spaces=True, pause=0.01)
                    send_keys("{ENTER}", pause=0.01)
            except RuntimeError:
                time.sleep(self.field_settle_sec)
                continue
            time.sleep(self.field_settle_sec)

            last_read = self._read_coordinate_value(spec)
            if math.isclose(last_read, value, rel_tol=0.0, abs_tol=tolerance):
                return

        raise RuntimeError(
            f"Failed to set {spec.name} reliably: expected {value}, read back {last_read}. "
            "Check coordinate_input_mode and click coordinates for this field."
        )

    def _read_coordinate_value(self, spec: ParameterSpec) -> float:
        last_raw = ""
        interaction_mode, wrapper, point = self._resolve_coordinate_interaction(spec)

        for _ in range(3):
            try:
                if interaction_mode == "message":
                    raw = self._read_window_text(wrapper)
                else:
                    self._clear_clipboard_text()
                    self._activate_coordinate_field(interaction_mode, wrapper, point)
                    send_keys("^a^c", pause=0.01)
                    time.sleep(self.field_settle_sec)
                    raw = self._read_clipboard_text()
            except RuntimeError:
                time.sleep(self.field_settle_sec)
                continue
            if raw:
                normalized = raw.replace(",", ".").strip()
                return float(normalized)
            last_raw = raw
        raise RuntimeError(f"Failed reading clipboard text for {spec.name}: {last_raw!r}")

    def _verify_expected_values(self, params: List[ParameterSpec]) -> None:
        mismatches = []
        for spec in params:
            expected = self._quantize_param_value(
                spec, float(np.clip(spec.value, spec.min_value, spec.max_value))
            )
            actual = self._read_coordinate_value(spec)
            unit = 10 ** (-spec.decimals)
            tolerance = max(unit * 0.5, 1e-6)
            if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance):
                mismatches.append((spec.name, expected, actual))

        if mismatches:
            details = "; ".join(
                f"{name}: expected {expected}, actual {actual}"
                for name, expected, actual in mismatches
            )
            raise RuntimeError(
                "Coordinate field state mismatch after write. "
                f"This usually means clicks hit the wrong input box. {details}"
            )

    def capture_click_positions(self, only_param: Optional[str] = None) -> None:
        if self.settings_win is None:
            raise RuntimeError("settings window is not connected")

        rect = self.settings_win.rectangle()
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        print(
            f"settings_window_rect: left={rect.left}, top={rect.top}, "
            f"right={rect.right}, bottom={rect.bottom}"
        )
        print("Move mouse to each parameter input field and press Enter.")
        print("Generated values are relative to settings window top-left.")

        target_params = self.params
        if only_param:
            target_params = [p for p in self.params if p.name == only_param]
            if not target_params:
                raise ValueError(f"Unknown parameter for --capture-click: {only_param}")

        snippets = []
        for param in target_params:
            input(f"[{param.name}] place mouse over input field, then press Enter...")
            abs_x, abs_y = self._cursor_pos()
            rel_x = abs_x - rect.left
            rel_y = abs_y - rect.top
            in_bounds = (0 <= rel_x < width) and (0 <= rel_y < height)
            snippets.append((param.name, rel_x, rel_y, abs_x, abs_y, in_bounds))

        print("\nSuggested JSON updates:")
        for name, rel_x, rel_y, abs_x, abs_y, in_bounds in snippets:
            suffix = "" if in_bounds else " [OUT_OF_BOUNDS]"
            print(
                f"{name}: click_x={rel_x}, click_y={rel_y} "
                f"(abs_x={abs_x}, abs_y={abs_y}){suffix}"
            )

    def capture_initial_values(self) -> Dict[str, float]:
        captured = self._read_script_control_values(self.params)
        print("Reading current values from settings fields...")
        for param in self.params:
            if param.name not in captured:
                raise RuntimeError(f"Script Control did not return a value for {param.name}")
            value = captured[param.name]
            captured[param.name] = value
            print(f"{param.name}: {value}")
        return captured

    def write_initial_values_to_config(self, config_path: str, values: Dict[str, float]) -> None:
        path = Path(config_path)
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        parameters = cfg.get("parameters", {})
        for name, value in values.items():
            if name in parameters:
                parameters[name]["initial"] = float(value)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
        print(f"Updated initial values in config: {path}")

    def connect_windows(self, allow_missing_settings: bool = False) -> None:
        self.app = Application(backend="uia").connect(
            title_re=self.cfg["movie_window_title_re"], timeout=15
        )
        self.movie_win = self.app.window(title_re=self.cfg["movie_window_title_re"])
        try:
            self.movie_win.wait("visible ready", timeout=15)
        except PywinautoTimeoutError:
            if self.movie_capture_mode != "dde_fbo":
                raise
        self.settings_win = None
        self.settings_backend = "uia"
        self.win32_app = None

    def list_edit_controls(self) -> None:
        if self.settings_win is not None:
            if self.settings_backend == "win32":
                edits = self.settings_win.descendants(class_name="Edit")
            else:
                edits = self.settings_win.descendants(control_type="Edit")

            print(
                f"Edit controls found in settings window ({self.settings_backend}): {len(edits)}"
            )
            for i, ctrl in enumerate(edits):
                info = ctrl.element_info
                print(
                    f"[{i}] title={info.name!r}, auto_id={info.automation_id!r}, class={info.class_name!r}"
                )
            if edits:
                return

        print("Settings window not found or has no Edit controls. Scanning desktop candidates...")
        candidates = []
        for win in Desktop(backend="uia").windows():
            title = (win.window_text() or "").strip()
            if not title:
                continue
            lower = title.lower()
            if not any(k in lower for k in ("ipg", "movie", "camera", "setting", "sensor")):
                continue
            try:
                edits = win.descendants(control_type="Edit")
                candidates.append((title, len(edits), win.is_visible(), win.is_enabled()))
            except Exception:
                continue

        for win in Desktop(backend="win32").windows():
            title = (win.window_text() or "").strip()
            if not title:
                continue
            lower = title.lower()
            if not any(k in lower for k in ("ipg", "movie", "camera", "setting", "sensor")):
                continue
            try:
                edits = win.descendants(class_name="Edit")
                candidates.append(
                    (f"{title} [win32]", len(edits), win.is_visible(), win.is_enabled())
                )
            except Exception:
                continue

        for title, edit_count, visible, enabled in sorted(candidates, key=lambda x: -x[1]):
            print(
                f"candidate title={title!r}, edits={edit_count}, visible={visible}, enabled={enabled}"
            )

    def _resolve_edit_control(self, spec: ParameterSpec):
        if spec.auto_id:
            return self.settings_win.child_window(auto_id=spec.auto_id, control_type="Edit")
        if spec.title:
            return self.settings_win.child_window(title=spec.title, control_type="Edit")
        if spec.field_index is not None:
            edits = self.settings_win.descendants(control_type="Edit")
            if spec.field_index < 0 or spec.field_index >= len(edits):
                raise IndexError(
                    f"field_index out of range for {spec.name}: {spec.field_index}, edits={len(edits)}"
                )
            return edits[spec.field_index]
        raise ValueError(
            f"Parameter {spec.name} requires one locator: auto_id/title/field_index"
        )

    def _set_edit_value(self, ctrl, value: float, decimals: int) -> None:
        text = f"{value:.{decimals}f}"
        ctrl.wait("visible enabled ready", timeout=10)
        self._safe_focus(ctrl)
        send_keys("^a{BACKSPACE}", pause=0.01)
        ctrl.type_keys(text, with_spaces=True, set_foreground=False)
        send_keys("{ENTER}", pause=0.01)

    @staticmethod
    def _quantize_value(value: float, decimals: int) -> float:
        return float(f"{float(value):.{decimals}f}")

    @staticmethod
    def _safe_focus(ctrl) -> None:
        try:
            ctrl.set_focus()
        except Exception:
            return

    def _quantize_param_value(self, spec: ParameterSpec, value: float) -> float:
        clipped = float(np.clip(value, spec.min_value, spec.max_value))
        return self._quantize_value(clipped, spec.decimals)

    def _apply_single_param(self, p: ParameterSpec, verify_all: bool = False) -> None:
        p.value = self._quantize_param_value(p, p.value)
        self._apply_script_control_params([p])

    def apply_params(self, params: List[ParameterSpec]) -> None:
        self._apply_script_control_params(params)

    def _get_movie_capture_bbox(self) -> Tuple[int, int, int, int]:
        rect = self.movie_win.rectangle()
        if self.movie_capture_mode != "client":
            return rect.left, rect.top, rect.right, rect.bottom

        try:
            import win32gui  # type: ignore

            hwnd = int(self.movie_win.handle)
            client_left, client_top, client_right, client_bottom = win32gui.GetClientRect(hwnd)
            origin_x, origin_y = win32gui.ClientToScreen(hwnd, (0, 0))
            return (
                int(origin_x + client_left),
                int(origin_y + client_top),
                int(origin_x + client_right),
                int(origin_y + client_bottom),
            )
        except Exception:
            return rect.left, rect.top, rect.right, rect.bottom

    def _get_movie_capture_size(self) -> Tuple[int, int]:
        if self.movie_capture_mode == "dde_fbo":
            return self._get_movie_dde_view_size()

        left, top, right, bottom = self._get_movie_capture_bbox()
        width = int(right - left)
        height = int(bottom - top)
        if width <= 0 or height <= 0:
            raise RuntimeError(f"Invalid movie capture size: {width}x{height}")
        return width, height

    def _get_movie_dde_view_size(self) -> Tuple[int, int]:
        script_path = self.output_dir / "movie_size_probe_dde.tcl"
        result_path = self.output_dir / "movie_size_probe_dde.txt"
        script_text = "\n".join(
            [
                f'set out [open "{result_path.as_posix()}" w]',
                "proc emit {text} {",
                "    global out",
                "    puts $out $text",
                "}",
                "set rc [catch {send IPG-MOVIE {",
                "    set vno $View(ev.view)",
                "    set wi [dict get $View($vno) Width]",
                "    set he [dict get $View($vno) Height]",
                "    list $wi $he",
                "}} msg]",
                'emit "rc=$rc"',
                'emit "msg_begin"',
                "emit $msg",
                'emit "msg_end"',
                "close $out",
                "",
            ]
        )
        script_path.write_text(script_text, encoding="utf-8")
        try:
            result_path.unlink()
        except FileNotFoundError:
            pass

        try:
            import win32ui  # noqa: F401
            import dde  # type: ignore
        except Exception as exc:
            raise RuntimeError("movie size probe requires pywin32 DDE support") from exc

        server = None
        try:
            server = dde.CreateServer()
            server.Create(f"CopilotMovieSizeProbe.{uuid.uuid4().hex}")
            conv = dde.CreateConversation(server)
            conv.ConnectTo(self.script_control_dde_service, self.script_control_dde_topic)
            conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
        except Exception as exc:
            raise RuntimeError(f"movie size probe RunScript failed: {exc}") from exc
        finally:
            if server is not None:
                try:
                    server.Shutdown()
                except Exception:
                    pass

        deadline = time.time() + self.script_control_timeout_sec
        while time.time() < deadline:
            if result_path.exists():
                text = result_path.read_text(encoding="utf-8", errors="replace")
                if self._is_script_control_result_complete(text):
                    rc, msg = self._parse_script_control_result_text(text)
                    if rc != 0:
                        raise RuntimeError(f"movie size probe failed: {msg}")
                    parts = str(msg).split()
                    if len(parts) != 2:
                        raise RuntimeError(f"movie size probe returned unexpected payload: {msg}")
                    width = int(parts[0])
                    height = int(parts[1])
                    if width <= 0 or height <= 0:
                        raise RuntimeError(f"movie size probe returned invalid size: {width}x{height}")
                    return width, height
            time.sleep(0.05)

        raise RuntimeError("Timed out waiting for movie size probe result")

    def _prepare_movie_window_for_capture(self) -> None:
        if self.movie_win is None:
            raise RuntimeError("movie window is not connected")

        try:
            self.movie_win.set_focus()
        except Exception:
            pass

        try:
            import win32con  # type: ignore
            import win32gui  # type: ignore

            hwnd = int(self.movie_win.handle)
            if hwnd:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )
                win32gui.SetForegroundWindow(hwnd)
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_NOTOPMOST,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )
        except Exception:
            pass

        time.sleep(0.15)

    def _crop_to_reference_aspect(self, image):
        if not self.movie_match_reference_aspect:
            return image

        img_w, img_h = image.size
        if img_w <= 0 or img_h <= 0:
            return image

        ref_h, ref_w = self.real_img.shape[:2]
        target_ratio = ref_w / max(1, ref_h)
        current_ratio = img_w / max(1, img_h)

        if math.isclose(current_ratio, target_ratio, rel_tol=0.0, abs_tol=1e-6):
            return image

        if current_ratio > target_ratio:
            new_w = max(1, int(round(img_h * target_ratio)))
            new_h = img_h
            if self.movie_aspect_crop_anchor == "center":
                left = max(0, int(round((img_w - new_w) / 2)))
            else:
                left = 0
            top = 0
        else:
            new_w = img_w
            new_h = max(1, int(round(img_w / target_ratio)))
            left = 0
            if self.movie_aspect_crop_anchor == "center":
                top = max(0, int(round((img_h - new_h) / 2)))
            else:
                top = 0

        return image.crop((left, top, left + new_w, top + new_h))

    def _detect_content_top_offset(self, image) -> int:
        rgb = np.array(image.convert("RGB"), dtype=np.int16)
        if rgb.ndim != 3 or rgb.shape[0] < 8:
            return 0

        max_scan = min(max(40, int(rgb.shape[0] * 0.2)), 160)
        baseline = rgb[0]
        diffs = np.mean(np.abs(rgb[:max_scan] - baseline), axis=(1, 2))
        threshold = max(35.0, float(np.median(diffs[: max(5, max_scan // 4)]) + 40.0))
        consecutive = 4
        for idx in range(1, max_scan - consecutive + 1):
            if np.all(diffs[idx : idx + consecutive] > threshold):
                return int(idx)
        return 0

    def _crop_movie_content(self, image):
        active_crop = (
            self.movie_dde_content_crop if self.movie_capture_mode == "dde_fbo" else self.movie_content_crop
        )
        if active_crop is not None:
            if not isinstance(active_crop, list) or len(active_crop) != 4:
                raise ValueError("movie_content_crop must be [left, top, right, bottom]")
            l, t, r, b = [int(v) for v in active_crop]
            img_w, img_h = image.size
            if r <= 0:
                r = img_w + r
            if b <= 0:
                b = img_h + b
            if l < 0 or t < 0 or r <= l or b <= t or r > img_w or b > img_h:
                raise ValueError("movie_content_crop is invalid")
            return image.crop((l, t, r, b))

        if not self.movie_auto_crop_content:
            return image

        img_w, img_h = image.size
        if img_w <= 0 or img_h <= 0:
            return image

        top = self._detect_content_top_offset(image)
        cropped = image.crop((0, top, img_w, img_h)) if top > 0 else image
        return self._crop_to_reference_aspect(cropped)

    def _preflight_capture_aspect_ratio(self) -> None:
        raw_w, raw_h = self._get_movie_capture_size()
        ref_h, ref_w = self.real_img.shape[:2]

        active_crop = (
            self.movie_dde_content_crop if self.movie_capture_mode == "dde_fbo" else self.movie_content_crop
        )

        if active_crop is not None:
            l, t, r, b = [int(v) for v in active_crop]
            if r <= 0:
                r = raw_w + r
            if b <= 0:
                b = raw_h + b
            crop_w = int(r - l)
            crop_h = int(b - t)
            if l < 0 or t < 0 or crop_w <= 0 or crop_h <= 0 or r > raw_w or b > raw_h:
                raise RuntimeError(
                    "movie_content_crop is invalid for current capture size: "
                    f"crop={active_crop}, capture={raw_w}x{raw_h}"
                )
            if crop_w * ref_h != ref_w * crop_h:
                raise RuntimeError(
                    "Configured movie_content_crop aspect ratio does not match real_image: "
                    f"cropped={crop_w}x{crop_h}, real={ref_w}x{ref_h}"
                )
            print(
                "Capture aspect preflight: "
                f"raw={raw_w}x{raw_h}, cropped={crop_w}x{crop_h}, real={ref_w}x{ref_h}"
            )
            return

        if self.movie_auto_crop_content and self.movie_match_reference_aspect:
            print(
                "Capture aspect preflight: auto crop with reference aspect matching enabled; "
                f"raw={raw_w}x{raw_h}, real={ref_w}x{ref_h}"
            )
            return

        if raw_w * ref_h != ref_w * raw_h:
            raise RuntimeError(
                "Current movie capture aspect ratio does not match real_image: "
                f"captured={raw_w}x{raw_h}, real={ref_w}x{ref_h}"
            )
        print(
            "Capture aspect preflight: "
            f"raw={raw_w}x{raw_h}, real={ref_w}x{ref_h}"
        )

    def _capture_movie_via_dde_fbo(self, tag: str) -> Path:
        script_path = self.output_dir / f"{tag}_movie_capture_dde.tcl"
        result_path = self.output_dir / f"{tag}_movie_capture_dde.txt"
        out_path = self.output_dir / f"{tag}.png"

        script_text = "\n".join(
            [
                f'set out [open "{result_path.as_posix()}" w]',
                "proc emit {text} {",
                "    global out",
                "    puts $out $text",
                "}",
                "set rc [catch {send IPG-MOVIE {",
                "    set vno $View(ev.view)",
                "    set wi [dict get $View($vno) Width]",
                "    set he [dict get $View($vno) Height]",
                "    set captureFBO [FBO new $wi $he -tex rgb -noclear]",
                "    set update_rc [catch {",
                "        FBO begin $captureFBO",
                "        UpdateView $vno",
                "        FBO end",
                "    } update_msg]",
                "    catch {FBO end}",
                "    if {$update_rc != 0} {",
                "        catch {FBO delete $captureFBO}",
                "        error $update_msg",
                "    }",
                "    catch {image delete probeImg}",
                "    image create photo probeImg -width $wi -height $he",
                "    gl bindframebuffer_read $captureFBO",
                "    gl readpixels 0 0 probeImg",
                f'    probeImg write "{out_path.as_posix()}" -format png',
                "    catch {gl bindframebuffer_read 0}",
                "    catch {FBO delete $captureFBO}",
                "}} msg]",
                'emit "rc=$rc"',
                'emit "msg_begin"',
                "emit $msg",
                'emit "msg_end"',
                "close $out",
                "",
            ]
        )
        script_path.write_text(script_text, encoding="utf-8")
        try:
            result_path.unlink()
        except FileNotFoundError:
            pass

        try:
            import win32ui  # noqa: F401
            import dde  # type: ignore
        except Exception as exc:
            raise RuntimeError("movie dde_fbo capture requires pywin32 DDE support") from exc

        server = None
        try:
            server = dde.CreateServer()
            server.Create(f"CopilotMovieCapture.{uuid.uuid4().hex}")
            conv = dde.CreateConversation(server)
            conv.ConnectTo(self.script_control_dde_service, self.script_control_dde_topic)
            conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
        except Exception as exc:
            raise RuntimeError(f"movie dde_fbo RunScript failed: {exc}") from exc
        finally:
            if server is not None:
                try:
                    server.Shutdown()
                except Exception:
                    pass

        deadline = time.time() + self.script_control_timeout_sec
        while time.time() < deadline:
            if result_path.exists():
                text = result_path.read_text(encoding="utf-8", errors="replace")
                if self._is_script_control_result_complete(text):
                    rc, msg = self._parse_script_control_result_text(text)
                    if rc != 0:
                        raise RuntimeError(f"movie dde_fbo capture failed: {msg}")
                    with Image.open(out_path) as raw_img:
                        img = self._crop_movie_content(raw_img.copy())
                    img.save(out_path)
                    return out_path
            time.sleep(0.05)

        raise RuntimeError("Timed out waiting for movie dde_fbo capture result")

    def capture_movie(self, tag: str) -> Path:
        if self.movie_capture_mode == "dde_fbo":
            return self._capture_movie_via_dde_fbo(tag)

        self._prepare_movie_window_for_capture()
        left, top, right, bottom = self._get_movie_capture_bbox()
        last_error = None
        img = None
        for _ in range(3):
            try:
                img = ImageGrab.grab(bbox=(left, top, right, bottom))
                break
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        if img is None:
            raise last_error
        img = self._crop_movie_content(img)
        out = self.output_dir / f"{tag}.png"
        img.save(out)
        return out

    def _snapshot_values(self) -> Dict[str, float]:
        return {p.name: p.value for p in self.params}

    def _apply_value_map(self, values: Dict[str, float]) -> None:
        touched = False
        touched_params: List[ParameterSpec] = []
        for param in self.params:
            if param.name not in values:
                continue
            param.value = float(values[param.name])
            touched_params.append(param)
            touched = True
        if touched_params:
            self._apply_script_control_params(touched_params)
        if touched:
            time.sleep(self.settle_sec)

    def _make_history_entry(
        self,
        iteration: int,
        total_detail: TotalScoreDetail,
        img_path: Path,
        accepted: bool,
        failed_reason: Optional[str] = None,
        meta: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        entry: Dict[str, object] = {
            "iter": iteration,
            "total_score": total_detail.total_score,
            "compared_board_count": total_detail.compared_board_count,
            "degrade_penalty": total_detail.degrade_penalty,
            "has_critical_degrade": total_detail.has_critical_degrade,
            "degraded_boards": total_detail.degraded_boards,
            "board_scores": [
                {
                    "board_id": s.board_id,
                    "board_type": s.board_type,
                    "compared": s.compared,
                    "reference_visible": s.reference_visible,
                    "sim_visible": s.sim_visible,
                    "score": s.total_score,
                    "rmse": s.rmse,
                    "mean_error": s.mean_error,
                    "max_error": s.max_error,
                    "miss_rate": s.miss_rate,
                    "matched_point_count": s.matched_point_count,
                    "failed_reason": s.failed_reason,
                }
                for s in total_detail.board_scores
            ],
            "accepted": accepted,
            "failed_reason": failed_reason or total_detail.failed_reason,
            "image": str(img_path),
            "values": self._snapshot_values(),
        }
        if meta:
            entry.update(meta)
        return entry

    def _extract_roi(
        self, image: np.ndarray, roi: Optional[Tuple[int, int, int, int]]
    ) -> Tuple[np.ndarray, Tuple[int, int]]:
        if roi is None:
            return image, (0, 0)

        x, y, width, height = roi
        img_h, img_w = image.shape[:2]
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(img_w, x + width)
        y1 = min(img_h, y + height)
        if x0 >= x1 or y0 >= y1:
            raise ValueError("roi is outside image bounds")
        return image[y0:y1, x0:x1], (x0, y0)

    def _prepare_eval_image(self, image: np.ndarray) -> np.ndarray:
        source_h, source_w = image.shape[:2]
        target_h, target_w = self.real_img.shape[:2]
        if source_h <= 0 or source_w <= 0:
            raise ValueError("image has invalid shape")
        if source_w * target_h != target_w * source_h:
            raise RuntimeError(
                "Captured image aspect ratio does not match real_image: "
                f"captured={source_w}x{source_h}, real={target_w}x{target_h}"
            )
        if image.shape != self.real_img.shape:
            target_w = int(self.real_img.shape[1])
            target_h = int(self.real_img.shape[0])
            if self.keep_aspect_resize:
                h, w = image.shape[:2]
                scale = min(target_w / max(1, w), target_h / max(1, h))
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                resized = cv2.resize(image, (new_w, new_h))
                canvas = np.zeros((target_h, target_w), dtype=resized.dtype)
                off_x = (target_w - new_w) // 2
                off_y = (target_h - new_h) // 2
                canvas[off_y : off_y + new_h, off_x : off_x + new_w] = resized
                image = canvas
            else:
                image = cv2.resize(image, (target_w, target_h))
        return image

    def _get_eval_transform(self, image_shape: Tuple[int, int]) -> EvalImageTransform:
        source_h, source_w = image_shape[:2]
        target_h = int(self.real_img.shape[0])
        target_w = int(self.real_img.shape[1])

        if source_h == target_h and source_w == target_w:
            return EvalImageTransform(
                scale_x=1.0,
                scale_y=1.0,
                offset_x=0,
                offset_y=0,
                eval_width=target_w,
                eval_height=target_h,
                source_width=source_w,
                source_height=source_h,
            )

        if self.keep_aspect_resize:
            scale = min(target_w / max(1, source_w), target_h / max(1, source_h))
            eval_w = max(1, int(round(source_w * scale)))
            eval_h = max(1, int(round(source_h * scale)))
            return EvalImageTransform(
                scale_x=scale,
                scale_y=scale,
                offset_x=(target_w - eval_w) // 2,
                offset_y=(target_h - eval_h) // 2,
                eval_width=eval_w,
                eval_height=eval_h,
                source_width=source_w,
                source_height=source_h,
            )

        return EvalImageTransform(
            scale_x=target_w / max(1, source_w),
            scale_y=target_h / max(1, source_h),
            offset_x=0,
            offset_y=0,
            eval_width=target_w,
            eval_height=target_h,
            source_width=source_w,
            source_height=source_h,
        )

    @staticmethod
    def _get_annotation_palette() -> List[Tuple[int, int, int]]:
        return [
            (70, 80, 230),
            (60, 170, 90),
            (220, 110, 60),
            (180, 60, 180),
            (70, 170, 200),
            (200, 200, 70),
            (80, 220, 120),
            (255, 180, 80),
            (80, 180, 255),
            (220, 120, 255),
            (255, 120, 120),
            (120, 255, 255),
        ]

    def _map_eval_points_to_source(
        self, points: np.ndarray, transform: EvalImageTransform
    ) -> np.ndarray:
        reshaped = points.reshape(-1, 2).astype(np.float32)
        mapped = np.empty_like(reshaped)
        mapped[:, 0] = (reshaped[:, 0] - float(transform.offset_x)) / float(transform.scale_x)
        mapped[:, 1] = (reshaped[:, 1] - float(transform.offset_y)) / float(transform.scale_y)
        mapped[:, 0] = np.clip(mapped[:, 0], 0, max(0, transform.source_width - 1))
        mapped[:, 1] = np.clip(mapped[:, 1], 0, max(0, transform.source_height - 1))
        return mapped.reshape(points.shape)

    @staticmethod
    def _draw_annotated_label(
        image: np.ndarray,
        text: str,
        anchor: Tuple[int, int],
        color: Tuple[int, int, int],
    ) -> None:
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.62
        thickness = 2
        (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
        x = max(8, int(anchor[0]))
        y = max(text_h + 12, int(anchor[1]))
        top_left = (x - 4, y - text_h - 8)
        bottom_right = (x + text_w + 6, y + baseline - 2)
        cv2.rectangle(image, top_left, bottom_right, color, -1)
        cv2.putText(
            image,
            text,
            (x, y - 4),
            font,
            scale,
            (16, 16, 16),
            thickness,
            cv2.LINE_AA,
        )

    def annotate_existing_image(
        self,
        image_path: Path,
        output_path: Optional[Path] = None,
    ) -> Tuple[Path, List[BoardScoreDetail]]:
        if self.real_detections is None:
            self.real_detections = self._detect_reference_boards()

        sim_gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        sim_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if sim_gray is None or sim_bgr is None:
            raise RuntimeError(f"Failed reading screenshot: {image_path}")

        transform = self._get_eval_transform(sim_gray.shape[:2])
        sim_prepared = self._prepare_eval_image(sim_gray)
        sim_score_img = self._build_sim_eval_image(sim_gray)
        palette = self._get_annotation_palette()
        board_scores: List[BoardScoreDetail] = []

        header = f"{Path(self.cfg['real_image']).name} compare on {image_path.name}"
        self._draw_annotated_label(sim_bgr, header, (12, 26), (245, 245, 245))

        legend_y = 62
        for index, board in enumerate(self.boards):
            color = palette[index % len(palette)]
            detection_img = sim_prepared if board.board_type == "custom_groundmaker" else sim_score_img
            sim_detection = self._detect_board(detection_img, board)
            real_detection = self.real_detections[board.board_id]
            score = self._score_board(board, real_detection, sim_detection)
            board_scores.append(score)

            if sim_detection.success and sim_detection.ordered_points.size > 0:
                mapped_points = self._map_eval_points_to_source(
                    sim_detection.ordered_points, transform
                ).reshape(-1, 2)
                bbox = self._expand_bbox(
                    self._points_bbox(mapped_points),
                    sim_gray.shape[:2],
                    ratio=0.08,
                    min_pad=10,
                )
                x, y, width, height = bbox
                cv2.rectangle(sim_bgr, (x, y), (x + width, y + height), color, 2)
                for point in mapped_points:
                    cv2.circle(
                        sim_bgr,
                        (int(round(float(point[0]))), int(round(float(point[1])))),
                        3,
                        color,
                        -1,
                    )
                self._draw_annotated_label(
                    sim_bgr,
                    board.board_id,
                    (x + 2, max(18, y - 10)),
                    color,
                )

            legend_text = (
                f"{board.board_id}: {score.total_score:.3f}"
                if score.compared
                else f"{board.board_id}: skipped"
            )
            self._draw_annotated_label(sim_bgr, legend_text, (12, legend_y), color)
            legend_y += 30

        final_output = output_path or image_path.with_name(f"{image_path.stem}_annotated.png")
        final_output.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(final_output), sim_bgr)
        return final_output, board_scores

    def _build_sim_eval_image(self, captured_gray: np.ndarray) -> np.ndarray:
        eval_image = self._prepare_eval_image(captured_gray)
        if self.comparison_mode == "direct":
            return eval_image

        residual = cv2.absdiff(eval_image, self.real_img)
        if self.overlay_residual_blur and self.overlay_residual_blur > 1:
            blur_size = int(self.overlay_residual_blur)
            if blur_size % 2 == 0:
                blur_size += 1
            residual = cv2.GaussianBlur(residual, (blur_size, blur_size), 0)

        if self.overlay_residual_threshold > 0:
            _, residual = cv2.threshold(
                residual,
                float(self.overlay_residual_threshold),
                255,
                cv2.THRESH_TOZERO,
            )

        if int(np.max(residual)) > 0:
            residual = cv2.normalize(residual, None, 0, 255, cv2.NORM_MINMAX)
        return residual.astype(np.uint8)

    @staticmethod
    def _preprocess_variants(gray: np.ndarray) -> List[np.ndarray]:
        variants = [gray]
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        variants.append(clahe.apply(gray))
        variants.append(cv2.GaussianBlur(gray, (3, 3), 0))
        return variants

    def _detect_checkerboard(
        self, gray_image: np.ndarray, board: BoardProfile
    ) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_img, offset = self._extract_roi(eval_image, board.roi)
        found = False
        corners = None
        for candidate in self._preprocess_variants(roi_img):
            flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
            found, corners = cv2.findChessboardCornersSB(candidate, board.board_size, flags=flags)
            if found and corners is not None:
                break

            fallback_flags = (
                cv2.CALIB_CB_ADAPTIVE_THRESH
                | cv2.CALIB_CB_NORMALIZE_IMAGE
                | cv2.CALIB_CB_FAST_CHECK
            )
            found, corners = cv2.findChessboardCorners(candidate, board.board_size, fallback_flags)
            if found and corners is not None:
                criteria = (
                    cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER,
                    30,
                    0.001,
                )
                corners = cv2.cornerSubPix(candidate, corners, (11, 11), (-1, -1), criteria)
                break

        if not found or corners is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="checkerboard",
                error_message="checkerboard not detected",
            )

        ordered_points = corners.reshape(-1, 2).astype(np.float32)
        ordered_points[:, 0] += float(offset[0])
        ordered_points[:, 1] += float(offset[1])
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(len(ordered_points)),
            ordered_points=ordered_points,
            board_type=board.board_type,
            roi_used=board.roi,
            detector="checkerboard",
        )

    def _detect_template_board(
        self,
        gray_image: np.ndarray,
        board: BoardProfile,
        search_mask: Optional[np.ndarray] = None,
    ) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_img, offset = self._extract_roi(eval_image, board.roi)
        roi_mask = None
        if search_mask is not None:
            prepared_mask = self._prepare_eval_image(search_mask)
            roi_mask, _ = self._extract_roi(prepared_mask, board.roi)
            roi_mask = roi_mask.astype(np.uint8)
            if int(cv2.countNonZero(roi_mask)) < max(8, board.min_match_count):
                return DetectionResult(
                    board_id=board.board_id,
                    success=False,
                    point_count=0,
                    ordered_points=np.empty((0, 2), dtype=np.float32),
                    board_type=board.board_type,
                    roi_used=board.roi,
                    detector="template",
                    error_message="search mask exhausted",
                )

        template_info = self.custom_templates.get(board.board_id)
        if template_info is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="missing template info",
            )

        kp_scene, des_scene = self.orb.detectAndCompute(roi_img, roi_mask)
        if des_scene is None or kp_scene is None or len(kp_scene) < board.min_match_count:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="insufficient custom board features",
            )

        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        raw_matches = bf.knnMatch(template_info["des"], des_scene, k=2)
        good_matches = []
        for pair in raw_matches:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < 0.75 * n.distance:
                good_matches.append(m)

        if len(good_matches) < max(8, board.min_match_count):
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="insufficient homography matches",
            )

        src_pts = np.float32([template_info["kp"][m.queryIdx].pt for m in good_matches]).reshape(
            -1, 1, 2
        )
        dst_pts = np.float32([kp_scene[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        homography, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 4.0)
        if homography is None or mask is None or int(mask.sum()) < 8:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template",
                error_message="custom board homography failed",
            )

        projected = cv2.perspectiveTransform(
            template_info["anchors"].reshape(-1, 1, 2), homography
        ).reshape(-1, 2)
        projected[:, 0] += float(offset[0])
        projected[:, 1] += float(offset[1])
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(projected.shape[0]),
            ordered_points=projected.astype(np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="template",
        )

    def _detect_template_match_board(
        self, gray_image: np.ndarray, board: BoardProfile
    ) -> DetectionResult:
        eval_image = self._prepare_eval_image(gray_image)
        roi_img, offset = self._extract_roi(eval_image, board.roi)

        template_info = self.custom_templates.get(board.board_id)
        if template_info is None:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template_match",
                error_message="missing template info",
            )

        template_gray = template_info["template"]
        if (
            roi_img.shape[0] < template_gray.shape[0]
            or roi_img.shape[1] < template_gray.shape[1]
        ):
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template_match",
                error_message="search roi smaller than template",
            )

        search_image = self._preprocess_template_match_image(roi_img, board)
        template_image = self._preprocess_template_match_image(template_gray, board)
        response = cv2.matchTemplate(search_image, template_image, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_location = cv2.minMaxLoc(response)
        if max_value < board.template_match_threshold:
            return DetectionResult(
                board_id=board.board_id,
                success=False,
                point_count=0,
                ordered_points=np.empty((0, 2), dtype=np.float32),
                board_type=board.board_type,
                roi_used=board.roi,
                detector="template_match",
                error_message=(
                    f"template match below threshold: {max_value:.3f} < "
                    f"{board.template_match_threshold:.3f}"
                ),
            )

        match_x = float(offset[0] + max_location[0])
        match_y = float(offset[1] + max_location[1])
        template_h, template_w = template_gray.shape[:2]
        anchors = np.array(
            [
                [match_x, match_y],
                [match_x + template_w - 1.0, match_y],
                [match_x + template_w - 1.0, match_y + template_h - 1.0],
                [match_x, match_y + template_h - 1.0],
                [match_x + template_w * 0.5, match_y + template_h * 0.5],
                [match_x + template_w * 0.25, match_y + template_h * 0.25],
                [match_x + template_w * 0.75, match_y + template_h * 0.25],
                [match_x + template_w * 0.75, match_y + template_h * 0.75],
                [match_x + template_w * 0.25, match_y + template_h * 0.75],
            ],
            dtype=np.float32,
        )
        return DetectionResult(
            board_id=board.board_id,
            success=True,
            point_count=int(anchors.shape[0]),
            ordered_points=anchors,
            board_type=board.board_type,
            roi_used=board.roi,
            detector="template_match",
        )

    def _detect_custom_groundmaker(
        self, gray_image: np.ndarray, board: BoardProfile
    ) -> DetectionResult:
        if board.custom_detector == "template_match":
            return self._detect_template_match_board(gray_image, board)
        return self._detect_template_board(gray_image, board)

    def _detect_board(self, gray_image: np.ndarray, board: BoardProfile) -> DetectionResult:
        if board.board_type == "checkerboard":
            primary = self._detect_checkerboard(gray_image, board)
            if primary.success:
                return primary
            if board.template_image:
                fallback = self._detect_template_board(gray_image, board)
                if fallback.success:
                    return fallback
            return primary
        if board.board_type == "custom_groundmaker":
            return self._detect_custom_groundmaker(gray_image, board)
        return DetectionResult(
            board_id=board.board_id,
            success=False,
            point_count=0,
            ordered_points=np.empty((0, 2), dtype=np.float32),
            board_type=board.board_type,
            roi_used=board.roi,
            detector="unknown",
            error_message=f"unsupported board_type: {board.board_type}",
        )

    def _detect_template_instances(
        self, gray_image: np.ndarray, board: BoardProfile, max_instances: int
    ) -> List[Tuple[DetectionResult, Tuple[int, int, int, int]]]:
        prepared = self._prepare_eval_image(gray_image)
        search_mask = np.full(prepared.shape[:2], 255, dtype=np.uint8)
        detections: List[Tuple[DetectionResult, Tuple[int, int, int, int]]] = []

        for _ in range(max_instances):
            detection = self._detect_template_board(prepared, board, search_mask=search_mask)
            if not detection.success or detection.ordered_points.size == 0:
                break

            bbox = self._expand_bbox(
                self._points_bbox(detection.ordered_points), prepared.shape[:2]
            )
            is_duplicate = any(self._bbox_iou(bbox, existing_bbox) >= 0.6 for _, existing_bbox in detections)
            x, y, width, height = bbox
            search_mask[y : y + height, x : x + width] = 0
            if is_duplicate:
                continue
            detections.append((detection, bbox))

        return detections

    def _detect_checkerboard_instances(
        self, gray_image: np.ndarray, board: BoardProfile, max_instances: int
    ) -> List[Tuple[DetectionResult, Tuple[int, int, int, int]]]:
        prepared = self._prepare_eval_image(gray_image)
        working = prepared.copy()
        detections: List[Tuple[DetectionResult, Tuple[int, int, int, int]]] = []

        for _ in range(max_instances):
            detection = self._detect_checkerboard(working, board)
            if not detection.success or detection.ordered_points.size == 0:
                break

            geometry_ratio, mean_step = self._checkerboard_geometry_ratio(detection, board)
            if geometry_ratio > 6.0:
                suppress_radius = max(8, int(round(mean_step * 0.9)))
                for point in detection.ordered_points.reshape(-1, 2):
                    cv2.circle(
                        working,
                        (int(round(float(point[0]))), int(round(float(point[1])))),
                        suppress_radius,
                        0,
                        -1,
                    )
                continue

            bbox = self._expand_bbox(
                self._points_bbox(detection.ordered_points), prepared.shape[:2]
            )
            is_duplicate = any(self._bbox_iou(bbox, existing_bbox) >= 0.6 for _, existing_bbox in detections)
            x, y, width, height = bbox
            working[y : y + height, x : x + width] = 0
            if is_duplicate:
                continue
            detections.append((detection, bbox))

        return detections

    @staticmethod
    def _checkerboard_geometry_ratio(
        detection: DetectionResult, board: BoardProfile
    ) -> Tuple[float, float]:
        if board.board_size is None or detection.ordered_points.size == 0:
            return float("inf"), 0.0
        cols, rows = board.board_size
        try:
            points = detection.ordered_points.reshape(rows, cols, 2)
        except ValueError:
            return float("inf"), 0.0

        dx = np.linalg.norm(points[:, 1:, :] - points[:, :-1, :], axis=2)
        dy = np.linalg.norm(points[1:, :, :] - points[:-1, :, :], axis=2)
        steps = np.concatenate([dx.ravel(), dy.ravel()])
        if steps.size == 0:
            return float("inf"), 0.0
        min_step = float(np.min(steps))
        max_step = float(np.max(steps))
        mean_step = float(np.mean(steps))
        if min_step <= 1e-6:
            return float("inf"), mean_step
        return max_step / min_step, mean_step

    def propose_boards_config(
        self,
        config_path: str,
        output_path: Optional[str] = None,
        preview_path: Optional[str] = None,
    ) -> Tuple[Path, Path, List[dict]]:
        config_file = Path(config_path)
        with open(config_file, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)

        proposal_file = Path(output_path) if output_path else config_file.with_name(
            f"{config_file.stem}.proposed{config_file.suffix}"
        )
        resolved_output_dir = _resolve_config_output_dir(cfg, config_file)
        preview_file = Path(preview_path) if preview_path else resolved_output_dir / "board_proposal_preview.png"
        proposal_file.parent.mkdir(parents=True, exist_ok=True)
        preview_file.parent.mkdir(parents=True, exist_ok=True)

        proposed_boards: List[dict] = []
        preview_items: List[dict] = []
        prepared_real = self._prepare_eval_image(self.real_img)

        checkerboard_groups: Dict[Tuple[int, int], List[dict]] = {}
        remaining_prototypes: List[dict] = []
        for prototype_cfg in cfg.get("boards", []):
            board_type = str(prototype_cfg.get("board_type", "")).strip().lower()
            if board_type == "checkerboard":
                raw_size = prototype_cfg.get("board_size")
                if isinstance(raw_size, list) and len(raw_size) == 2:
                    key = (int(raw_size[0]), int(raw_size[1]))
                    checkerboard_groups.setdefault(key, []).append(prototype_cfg)
                    continue
            remaining_prototypes.append(prototype_cfg)

        for board_size, family_cfgs in checkerboard_groups.items():
            family_board_id = str(family_cfgs[0].get("board_id", "checkerboard")).strip() or "checkerboard"
            prototype = next(
                (
                    board
                    for board in self.boards
                    if board.board_type == "checkerboard" and board.board_size == board_size
                ),
                None,
            )
            if prototype is None:
                continue

            max_instances = max(
                int(item.get("proposal_max_instances", 4)) for item in family_cfgs
            )
            candidate_entries = self._detect_checkerboard_instances(
                prepared_real, prototype, max_instances=max_instances
            )
            if not candidate_entries:
                fallback = self._detect_board(prepared_real, prototype)
                if fallback.success and fallback.ordered_points.size > 0:
                    bbox = self._expand_bbox(
                        self._points_bbox(fallback.ordered_points), prepared_real.shape[:2]
                    )
                    candidate_entries = [(fallback, bbox)]

            generic_prefix = (
                family_board_id if len(family_cfgs) == 1 else f"checkerboard_{board_size[0]}x{board_size[1]}"
            )
            prototype_cfg = dict(family_cfgs[0])
            for index, (detection, bbox) in enumerate(candidate_entries, start=1):
                candidate_cfg = dict(prototype_cfg)
                candidate_cfg["board_id"] = (
                    f"{generic_prefix}_{index:02d}" if len(candidate_entries) > 1 or len(family_cfgs) > 1 else generic_prefix
                )
                candidate_cfg["roi"] = [int(v) for v in bbox]
                proposed_boards.append(candidate_cfg)
                preview_items.append(
                    {
                        "board_id": str(candidate_cfg["board_id"]),
                        "board_type": prototype.board_type,
                        "bbox": bbox,
                        "points": detection.ordered_points.copy(),
                    }
                )

        for prototype_cfg in remaining_prototypes:
            board_id = str(prototype_cfg.get("board_id", "")).strip()
            prototype = next((board for board in self.boards if board.board_id == board_id), None)
            if prototype is None:
                continue

            default_instances = 1 if prototype.board_type == "custom_groundmaker" else 4
            max_instances = int(prototype_cfg.get("proposal_max_instances", default_instances))
            candidate_entries: List[Tuple[DetectionResult, Tuple[int, int, int, int]]] = []

            if prototype.template_image:
                candidate_entries = self._detect_template_instances(
                    prepared_real, prototype, max_instances=max_instances
                )
            else:
                single = self._detect_board(prepared_real, prototype)
                if single.success and single.ordered_points.size > 0:
                    bbox = self._expand_bbox(
                        self._points_bbox(single.ordered_points), prepared_real.shape[:2]
                    )
                    candidate_entries = [(single, bbox)]

            for index, (detection, bbox) in enumerate(candidate_entries, start=1):
                candidate_cfg = dict(prototype_cfg)
                candidate_cfg["board_id"] = f"{board_id}_{index:02d}" if max_instances > 1 else board_id
                candidate_cfg["roi"] = [int(v) for v in bbox]
                proposed_boards.append(candidate_cfg)
                preview_items.append(
                    {
                        "board_id": str(candidate_cfg["board_id"]),
                        "board_type": prototype.board_type,
                        "bbox": bbox,
                        "points": detection.ordered_points.copy(),
                    }
                )

        cfg["boards"] = proposed_boards
        with open(proposal_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)

        preview_image = cv2.cvtColor(prepared_real, cv2.COLOR_GRAY2BGR)
        palette = [
            (70, 80, 230),
            (60, 170, 90),
            (220, 110, 60),
            (180, 60, 180),
            (70, 170, 200),
            (200, 200, 70),
        ]
        for index, item in enumerate(preview_items, start=1):
            color = palette[(index - 1) % len(palette)]
            x, y, width, height = item["bbox"]
            cv2.rectangle(preview_image, (x, y), (x + width, y + height), color, 3)
            label = f"{index}:{item['board_id']}"
            cv2.putText(
                preview_image,
                label,
                (x, max(24, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                color,
                2,
                cv2.LINE_AA,
            )
            for point in item["points"].reshape(-1, 2):
                cv2.circle(
                    preview_image,
                    (int(round(float(point[0]))), int(round(float(point[1])))),
                    4,
                    color,
                    -1,
                )

        cv2.imwrite(str(preview_file), preview_image)
        print(f"Proposed boards config: {proposal_file}")
        print(f"Proposal preview image: {preview_file}")
        for item in preview_items:
            x, y, width, height = item["bbox"]
            print(
                f"{item['board_id']}: type={item['board_type']} roi=[{x}, {y}, {width}, {height}]"
            )
        return proposal_file, preview_file, proposed_boards

    def _detect_reference_boards(self) -> Dict[str, DetectionResult]:
        detections: Dict[str, DetectionResult] = {}
        visible_count = 0
        for board in self.boards:
            detection = self._detect_board(self.real_img, board)
            if self._is_visible(detection, board.min_detected_points):
                visible_count += 1
            detections[board.board_id] = detection
        if visible_count == 0:
            raise RuntimeError(
                "No boards are visible in reference image. "
                "Cannot optimize without comparable targets."
            )
        return detections

    def _score_board(
        self, board: BoardProfile, real_detection: DetectionResult, sim_detection: DetectionResult
    ) -> BoardScoreDetail:
        real_visible = self._is_visible(real_detection, board.min_detected_points)

        if self.compare_only_if_reference_visible and not real_visible:
            return BoardScoreDetail(
                board_id=board.board_id,
                board_type=board.board_type,
                success=True,
                compared=False,
                reference_visible=False,
                sim_visible=self._is_visible(sim_detection, board.min_detected_points),
                total_score=0.0,
                rmse=0.0,
                mean_error=0.0,
                max_error=0.0,
                miss_rate=0.0,
                matched_point_count=0,
                failed_reason="not visible in reference, skipped",
            )

        if not sim_detection.success:
            return BoardScoreDetail(
                board_id=board.board_id,
                board_type=board.board_type,
                success=False,
                compared=True,
                reference_visible=real_visible,
                sim_visible=False,
                total_score=board.fail_penalty,
                rmse=board.fail_penalty,
                mean_error=board.fail_penalty,
                max_error=board.fail_penalty,
                miss_rate=1.0,
                matched_point_count=0,
                failed_reason=sim_detection.error_message,
            )

        if not real_detection.success:
            return BoardScoreDetail(
                board_id=board.board_id,
                board_type=board.board_type,
                success=False,
                compared=True,
                reference_visible=False,
                sim_visible=self._is_visible(sim_detection, board.min_detected_points),
                total_score=board.fail_penalty,
                rmse=board.fail_penalty,
                mean_error=board.fail_penalty,
                max_error=board.fail_penalty,
                miss_rate=1.0,
                matched_point_count=0,
                failed_reason="reference board unavailable",
            )

        matched_points = min(real_detection.point_count, sim_detection.point_count)
        if matched_points < board.min_detected_points:
            miss_rate = 1.0 - (matched_points / max(1, real_detection.point_count))
            return BoardScoreDetail(
                board_id=board.board_id,
                board_type=board.board_type,
                success=False,
                compared=True,
                reference_visible=real_visible,
                sim_visible=self._is_visible(sim_detection, board.min_detected_points),
                total_score=board.fail_penalty,
                rmse=board.fail_penalty,
                mean_error=board.fail_penalty,
                max_error=board.fail_penalty,
                miss_rate=miss_rate,
                matched_point_count=matched_points,
                failed_reason="insufficient detected points",
            )

        real_points = real_detection.ordered_points[:matched_points]
        sim_points = sim_detection.ordered_points[:matched_points]
        deltas = sim_points - real_points
        distances = np.linalg.norm(deltas, axis=1)
        rmse = float(np.sqrt(np.mean(np.square(distances))))
        mean_error = float(np.mean(distances))
        max_error = float(np.max(distances))
        miss_rate = 1.0 - (matched_points / max(1, real_detection.point_count))
        total_score = rmse + board.alpha * miss_rate + board.beta * max_error

        return BoardScoreDetail(
            board_id=board.board_id,
            board_type=board.board_type,
            success=True,
            compared=True,
            reference_visible=real_visible,
            sim_visible=True,
            total_score=total_score,
            rmse=rmse,
            mean_error=mean_error,
            max_error=max_error,
            miss_rate=miss_rate,
            matched_point_count=matched_points,
        )

    def _aggregate_scores(
        self,
        board_scores: List[BoardScoreDetail],
        baseline_metrics: Optional[Dict[str, Dict[str, float]]],
    ) -> TotalScoreDetail:
        total_score = 0.0
        degrade_penalty = 0.0
        degraded_boards: List[str] = []
        has_critical_degrade = False
        compared_board_count = 0
        board_map = {b.board_id: b for b in self.boards}

        for score in board_scores:
            board = board_map[score.board_id]
            if not score.compared:
                continue

            compared_board_count += 1
            weighted = board.weight * score.total_score
            total_score += weighted

            if baseline_metrics is None:
                continue

            baseline = baseline_metrics.get(score.board_id)
            if baseline is None:
                continue

            degraded = False
            if not score.success:
                degraded = True
                degrade_penalty += 1.0
            else:
                rmse_delta = score.rmse - float(baseline.get("rmse", score.rmse))
                max_delta = score.max_error - float(
                    baseline.get("max_error", score.max_error)
                )
                miss_delta = score.miss_rate - float(
                    baseline.get("miss_rate", score.miss_rate)
                )
                if rmse_delta > board.degrade_threshold_rmse:
                    degraded = True
                if max_delta > board.degrade_threshold_max_error:
                    degraded = True
                if miss_delta > board.degrade_threshold_miss_rate:
                    degraded = True
                if degraded:
                    degrade_penalty += max(0.0, rmse_delta) + max(0.0, max_delta) + max(0.0, miss_delta)

            if degraded:
                degraded_boards.append(score.board_id)
                if board.critical:
                    has_critical_degrade = True

        if compared_board_count == 0:
            return TotalScoreDetail(
                success=False,
                total_score=self.no_signal_penalty,
                degrade_penalty=0.0,
                has_critical_degrade=False,
                degraded_boards=[],
                compared_board_count=0,
                board_scores=board_scores,
                failed_reason="no comparable boards in current frame",
            )

        total_score += self.degrade_lambda * degrade_penalty

        return TotalScoreDetail(
            success=not has_critical_degrade,
            total_score=total_score,
            degrade_penalty=degrade_penalty,
            has_critical_degrade=has_critical_degrade,
            degraded_boards=degraded_boards,
            compared_board_count=compared_board_count,
            board_scores=board_scores,
            failed_reason="critical board degraded" if has_critical_degrade else None,
        )

    def _as_baseline_metrics(
        self, total_detail: TotalScoreDetail
    ) -> Dict[str, Dict[str, float]]:
        baseline: Dict[str, Dict[str, float]] = {}
        for score in total_detail.board_scores:
            if not score.compared:
                continue
            baseline[score.board_id] = {
                "rmse": score.rmse,
                "max_error": score.max_error,
                "miss_rate": score.miss_rate,
            }
        return baseline

    def _priority_board_improvements(
        self,
        baseline_detail: TotalScoreDetail,
        candidate_detail: TotalScoreDetail,
    ) -> List[Tuple[str, float]]:
        if not self.priority_board_accept_ids:
            return []

        baseline_scores = {
            score.board_id: score
            for score in baseline_detail.board_scores
            if score.compared and score.success
        }
        candidate_scores = {
            score.board_id: score
            for score in candidate_detail.board_scores
            if score.compared and score.success
        }

        improvements: List[Tuple[str, float]] = []
        for board_id in self.priority_board_accept_ids:
            baseline_score = baseline_scores.get(board_id)
            candidate_score = candidate_scores.get(board_id)
            if baseline_score is None or candidate_score is None:
                continue
            improvement = baseline_score.total_score - candidate_score.total_score
            if improvement >= self.priority_board_accept_min_improvement:
                improvements.append((board_id, improvement))
        return improvements

    def _acceptance_decision(
        self,
        baseline_score: float,
        baseline_detail: TotalScoreDetail,
        candidate_score: float,
        candidate_detail: TotalScoreDetail,
    ) -> Tuple[bool, str]:
        if candidate_detail.compared_board_count <= 0:
            return False, "no_comparable_boards"
        if candidate_detail.has_critical_degrade:
            return False, "critical_degrade"
        if candidate_score + self.min_improve < baseline_score:
            return True, "total_score_improved"

        if (
            not self.priority_board_accept_ids
            or self.priority_board_accept_min_improvement <= 0.0
            or self.priority_board_accept_max_total_worsen <= 0.0
        ):
            return False, "total_score_not_improved"

        score_worsen = candidate_score - baseline_score
        if score_worsen > self.priority_board_accept_max_total_worsen:
            return False, "priority_worsen_limit_exceeded"

        improvements = self._priority_board_improvements(baseline_detail, candidate_detail)
        if len(improvements) < self.priority_board_accept_min_count:
            return False, "priority_board_improvement_insufficient"

        improvement_summary = ",".join(
            f"{board_id}:{improvement:.3f}" for board_id, improvement in improvements
        )
        return True, f"priority_board_override[{improvement_summary}]"

    def _is_joint_exploration_param(self, param_name: str) -> bool:
        return (
            param_name in self.joint_exploration_param_set
            and self.joint_exploration_max_single_worsen > 0.0
        )

    def _trial_multipliers_for_param(self, param_name: str) -> List[float]:
        if not self._is_joint_exploration_param(param_name):
            return [1.0]
        return self.joint_exploration_trial_multipliers

    def _joint_exploration_candidate_reason(
        self,
        param_name: str,
        baseline_score: float,
        candidate_detail: TotalScoreDetail,
        candidate_score: float,
    ) -> Optional[str]:
        if not self._is_joint_exploration_param(param_name):
            return None
        if candidate_detail.compared_board_count <= 0 or candidate_detail.has_critical_degrade:
            return None
        score_worsen = candidate_score - baseline_score
        if score_worsen > self.joint_exploration_max_single_worsen:
            return None
        return f"joint_exploration_candidate[{score_worsen:.3f}]"

    def _candidate_move_sort_key(self, move: Dict[str, object]) -> Tuple[int, int, float]:
        name = str(move["name"])
        return (
            0 if name in self.joint_exploration_param_set else 1,
            self.param_order_index.get(name, len(self.param_order_index)),
            float(move["score"]),
        )

    def evaluate(
        self, tag: str, baseline_metrics: Optional[Dict[str, Dict[str, float]]]
    ) -> Tuple[TotalScoreDetail, Path]:
        if self.real_detections is None:
            self.real_detections = self._detect_reference_boards()

        sim_path = self.capture_movie(tag)
        sim_img = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
        if sim_img is None:
            raise RuntimeError(f"Failed reading screenshot: {sim_path}")

        sim_prepared = self._prepare_eval_image(sim_img)
        sim_score_img = self._build_sim_eval_image(sim_img)
        board_scores: List[BoardScoreDetail] = []
        for board in self.boards:
            real_detection = self.real_detections[board.board_id]
            detection_img = sim_prepared if board.board_type == "custom_groundmaker" else sim_score_img
            sim_detection = self._detect_board(detection_img, board)
            board_scores.append(self._score_board(board, real_detection, sim_detection))

        total_detail = self._aggregate_scores(board_scores, baseline_metrics)
        return total_detail, sim_path

    def _build_result_payload(
        self,
        *,
        best_score: float,
        best_values: Dict[str, float],
        best_total_detail: TotalScoreDetail,
        best_img: Path,
        stop_reason: str,
        history: List[dict],
        in_progress: bool,
    ) -> dict:
        updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return {
            "boards": [
                {
                    "board_id": b.board_id,
                    "board_type": b.board_type,
                    "weight": b.weight,
                    "critical": b.critical,
                }
                for b in self.boards
            ],
            "comparison_mode": self.comparison_mode,
            "output_dir": str(self.output_dir),
            "best_score": best_score,
            "best_values": best_values,
            "best_metrics": {
                "compared_board_count": best_total_detail.compared_board_count,
                "degrade_penalty": best_total_detail.degrade_penalty,
                "has_critical_degrade": best_total_detail.has_critical_degrade,
                "degraded_boards": best_total_detail.degraded_boards,
                "board_scores": [
                    {
                        "board_id": s.board_id,
                        "board_type": s.board_type,
                        "compared": s.compared,
                        "reference_visible": s.reference_visible,
                        "sim_visible": s.sim_visible,
                        "score": s.total_score,
                        "rmse": s.rmse,
                        "mean_error": s.mean_error,
                        "max_error": s.max_error,
                        "miss_rate": s.miss_rate,
                        "matched_point_count": s.matched_point_count,
                        "failed_reason": s.failed_reason,
                    }
                    for s in best_total_detail.board_scores
                ],
            },
            "best_image": str(best_img),
            "live_log": str(self.live_log_path) if self.live_log_path else None,
            "run_session_id": self.run_session_id,
            "started_at": self.run_started_at,
            "updated_at": updated_at,
            "finished_at": None if in_progress else updated_at,
            "stop_reason": stop_reason,
            "history_count": len(history),
            "in_progress": in_progress,
            "history": history,
        }

    def _write_progress_result(
        self,
        *,
        best_score: float,
        best_values: Dict[str, float],
        best_total_detail: TotalScoreDetail,
        best_img: Path,
        stop_reason: str,
        history: List[dict],
        in_progress: bool,
    ) -> None:
        result = self._build_result_payload(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason=stop_reason,
            history=history,
            in_progress=in_progress,
        )
        with open(self.output_dir / "result.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    def _flush_progress_if_needed(
        self,
        *,
        best_score: float,
        best_values: Dict[str, float],
        best_total_detail: TotalScoreDetail,
        best_img: Path,
        stop_reason: str,
        history: List[dict],
    ) -> None:
        if len(history) % self.progress_flush_every != 0:
            return
        self._write_progress_result(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason=stop_reason,
            history=history,
            in_progress=True,
        )

    def optimize(self) -> dict:
        if self.real_detections is None:
            self.real_detections = self._detect_reference_boards()

        self._print_run_summary()
        self._preflight_capture_aspect_ratio()
        self.preflight_script_control()
        self.apply_params(self.params)
        best_total_detail, best_img = self.evaluate("iter_0000", baseline_metrics=None)
        best_score = best_total_detail.total_score
        best_baseline = self._as_baseline_metrics(best_total_detail)
        best_values = {p.name: p.value for p in self.params}
        stop_reason = "max_iters_reached"

        history = [
            {
                "iter": 0,
                "total_score": best_score,
                "compared_board_count": best_total_detail.compared_board_count,
                "degrade_penalty": best_total_detail.degrade_penalty,
                "has_critical_degrade": best_total_detail.has_critical_degrade,
                "degraded_boards": best_total_detail.degraded_boards,
                "board_scores": [
                    {
                        "board_id": s.board_id,
                        "board_type": s.board_type,
                        "compared": s.compared,
                        "reference_visible": s.reference_visible,
                        "sim_visible": s.sim_visible,
                        "score": s.total_score,
                        "rmse": s.rmse,
                        "mean_error": s.mean_error,
                        "max_error": s.max_error,
                        "miss_rate": s.miss_rate,
                        "matched_point_count": s.matched_point_count,
                        "failed_reason": s.failed_reason,
                    }
                    for s in best_total_detail.board_scores
                ],
                "accepted": True,
                "image": str(best_img),
                "values": best_values.copy(),
            }
        ]

        print(
            "iter=0 "
            f"total_score={best_score:.6f} "
            f"compared_boards={best_total_detail.compared_board_count} "
            f"degrade_penalty={best_total_detail.degrade_penalty:.6f} "
            f"{self._top_board_summary(best_total_detail)}"
        )
        self._write_progress_result(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason="running",
            history=history,
            in_progress=True,
        )

        it = 1
        while it <= self.max_iters:
            improved_in_iter = False
            base_values = self._snapshot_values()
            base_score = best_score
            candidate_moves: List[Dict[str, object]] = []

            for p in self.params:
                preferred_direction = self.preferred_directions.get(p.name, 1.0)
                trial_directions: List[float] = [preferred_direction, -preferred_direction]
                best_param_move: Optional[Dict[str, object]] = None
                seen_trial_values: set[float] = set()
                stop_param_search = False

                for direction in trial_directions:
                    for trial_multiplier in self._trial_multipliers_for_param(p.name):
                        trial_value = self._quantize_param_value(
                            p,
                            base_values[p.name] + direction * p.step * trial_multiplier,
                        )
                        if math.isclose(
                            trial_value,
                            base_values[p.name],
                            rel_tol=0.0,
                            abs_tol=1e-12,
                        ):
                            continue
                        if any(
                            math.isclose(trial_value, seen, rel_tol=0.0, abs_tol=1e-12)
                            for seen in seen_trial_values
                        ):
                            continue
                        seen_trial_values.add(trial_value)

                        try:
                            self._apply_value_map({p.name: trial_value})
                            total_detail, img_path = self.evaluate(
                                f"iter_{it:04d}_{p.name}_{'p' if direction > 0 else 'n'}",
                                baseline_metrics=best_baseline,
                            )
                            score = total_detail.total_score
                            accepted, accepted_reason = self._acceptance_decision(
                                baseline_score=base_score,
                                baseline_detail=best_total_detail,
                                candidate_score=score,
                                candidate_detail=total_detail,
                            )
                            joint_candidate_reason = None
                            if not accepted:
                                joint_candidate_reason = self._joint_exploration_candidate_reason(
                                    p.name,
                                    base_score,
                                    total_detail,
                                    score,
                                )
                            history.append(
                                self._make_history_entry(
                                    it,
                                    total_detail,
                                    img_path,
                                    accepted,
                                    meta={
                                        "phase": "single",
                                        "param": p.name,
                                        "trial": trial_value,
                                        "direction": "+" if direction > 0 else "-",
                                        "trial_multiplier": trial_multiplier,
                                        "accepted_reason": accepted_reason,
                                        "joint_candidate_reason": joint_candidate_reason,
                                    },
                                )
                            )
                            print(
                                f"iter={it} phase=single param={p.name} trial={trial_value:.{p.decimals}f} "
                                f"total_score={score:.6f} compared={total_detail.compared_board_count} "
                                f"degrade={total_detail.degrade_penalty:.6f} "
                                f"critical_degrade={total_detail.has_critical_degrade} "
                                f"accepted={accepted} accepted_reason={accepted_reason} "
                                f"joint_candidate={joint_candidate_reason or '-'} "
                                f"direction={'+' if direction > 0 else '-'} "
                                f"step_scale={trial_multiplier:g} "
                                f"{self._top_board_summary(total_detail)}"
                            )
                            self._flush_progress_if_needed(
                                best_score=best_score,
                                best_values=best_values,
                                best_total_detail=best_total_detail,
                                best_img=best_img,
                                stop_reason="running",
                                history=history,
                            )
                            self._apply_value_map({p.name: base_values[p.name]})
                        except RuntimeError as exc:
                            restored = self._recover_after_runtime_error(base_values)
                            history.append(
                                self._make_history_entry(
                                    it,
                                    best_total_detail,
                                    best_img,
                                    False,
                                    failed_reason=str(exc),
                                    meta={
                                        "phase": "single_runtime_error",
                                        "param": p.name,
                                        "trial": trial_value,
                                        "direction": "+" if direction > 0 else "-",
                                        "trial_multiplier": trial_multiplier,
                                        "recovered": restored,
                                    },
                                )
                            )
                            print(
                                f"iter={it} phase=single param={p.name} trial={trial_value:.{p.decimals}f} "
                                f"runtime_error={exc} recovered={restored}"
                            )
                            self._flush_progress_if_needed(
                                best_score=best_score,
                                best_values=best_values,
                                best_total_detail=best_total_detail,
                                best_img=best_img,
                                stop_reason="running",
                                history=history,
                            )
                            if not restored:
                                raise RuntimeError(
                                    f"Failed to recover after Script Control runtime error: {exc}"
                                )
                            it += 1
                            if it > self.max_iters:
                                stop_param_search = True
                                break
                            continue

                        eligible_for_joint = accepted or joint_candidate_reason is not None
                        if eligible_for_joint and (
                            best_param_move is None
                            or (
                                accepted
                                and not bool(best_param_move.get("accepted", False))
                            )
                            or score < float(best_param_move["score"])
                        ):
                            best_param_move = {
                                "name": p.name,
                                "value": trial_value,
                                "direction": direction,
                                "score": score,
                                "total_detail": total_detail,
                                "img_path": img_path,
                                "baseline": self._as_baseline_metrics(total_detail),
                                "accepted": accepted,
                                "joint_candidate_reason": joint_candidate_reason,
                            }

                        if accepted:
                            self.preferred_directions[p.name] = direction

                        it += 1
                        if it > self.max_iters:
                            stop_param_search = True
                            break
                        if (
                            accepted
                            and self.stop_after_first_accepted_direction
                            and not self._is_joint_exploration_param(p.name)
                        ):
                            stop_param_search = True
                            break

                    if stop_param_search:
                        break

                if best_param_move is None:
                    p.step = max(p.min_step, p.step * self.step_decay)
                else:
                    candidate_moves.append(best_param_move)

                if it > self.max_iters:
                    break

            accepted_params_in_pass: List[str] = []
            fallback_move: Optional[Dict[str, object]] = None
            accepted_candidate_moves = [
                move for move in candidate_moves if bool(move.get("accepted", False))
            ]
            if accepted_candidate_moves:
                fallback_move = min(
                    accepted_candidate_moves, key=lambda item: float(item["score"])
                )

            if candidate_moves and it <= self.max_iters:
                candidate_moves.sort(key=self._candidate_move_sort_key)
                joint_values = base_values.copy()
                joint_score = base_score
                joint_total_detail = best_total_detail
                joint_baseline = best_baseline
                joint_img = best_img

                self._apply_value_map_or_recover(
                    base_values,
                    "Failed to restore base values before joint phase",
                )

                for move in candidate_moves:
                    name = str(move["name"])
                    trial_value = float(move["value"])
                    previous_value = float(joint_values[name])

                    try:
                        self._apply_value_map({name: trial_value})
                        total_detail, img_path = self.evaluate(
                            f"iter_{it:04d}_joint_{name}",
                            baseline_metrics=joint_baseline,
                        )
                        score = total_detail.total_score
                        accepted, accepted_reason = self._acceptance_decision(
                            baseline_score=joint_score,
                            baseline_detail=joint_total_detail,
                            candidate_score=score,
                            candidate_detail=total_detail,
                        )
                        history.append(
                            self._make_history_entry(
                                it,
                                total_detail,
                                img_path,
                                accepted,
                                meta={
                                    "phase": "joint",
                                    "param": name,
                                    "trial": trial_value,
                                    "direction": "+" if float(move["direction"]) > 0 else "-",
                                    "joint_params": accepted_params_in_pass + [name],
                                    "accepted_reason": accepted_reason,
                                },
                            )
                        )
                        print(
                            f"iter={it} phase=joint param={name} trial={trial_value:.4f} "
                            f"total_score={score:.6f} compared={total_detail.compared_board_count} "
                            f"degrade={total_detail.degrade_penalty:.6f} "
                            f"critical_degrade={total_detail.has_critical_degrade} accepted={accepted} "
                            f"accepted_reason={accepted_reason} "
                            f"{self._top_board_summary(total_detail)}"
                        )
                        self._flush_progress_if_needed(
                            best_score=best_score,
                            best_values=best_values,
                            best_total_detail=best_total_detail,
                            best_img=best_img,
                            stop_reason="running",
                            history=history,
                        )

                        if accepted:
                            joint_values[name] = trial_value
                            joint_score = score
                            joint_total_detail = total_detail
                            joint_baseline = self._as_baseline_metrics(total_detail)
                            joint_img = img_path
                            accepted_params_in_pass.append(name)
                            improved_in_iter = True
                        else:
                            self._apply_value_map({name: previous_value})
                    except RuntimeError as exc:
                        restored = self._recover_after_runtime_error(joint_values)
                        history.append(
                            self._make_history_entry(
                                it,
                                joint_total_detail,
                                joint_img,
                                False,
                                failed_reason=str(exc),
                                meta={
                                    "phase": "joint_runtime_error",
                                    "param": name,
                                    "trial": trial_value,
                                    "direction": "+" if float(move["direction"]) > 0 else "-",
                                    "joint_params": accepted_params_in_pass + [name],
                                    "recovered": restored,
                                },
                            )
                        )
                        print(
                            f"iter={it} phase=joint param={name} trial={trial_value:.4f} "
                            f"runtime_error={exc} recovered={restored}"
                        )
                        self._flush_progress_if_needed(
                            best_score=best_score,
                            best_values=best_values,
                            best_total_detail=best_total_detail,
                            best_img=best_img,
                            stop_reason="running",
                            history=history,
                        )
                        if not restored:
                            raise RuntimeError(
                                f"Failed to recover after Script Control runtime error: {exc}"
                            )

                    it += 1
                    if it > self.max_iters:
                        break

                if accepted_params_in_pass:
                    best_score = joint_score
                    best_total_detail = joint_total_detail
                    best_baseline = joint_baseline
                    best_img = joint_img
                    best_values = joint_values.copy()
                    self._apply_value_map_or_recover(
                        joint_values,
                        "Failed to apply joint values after accepted joint update",
                    )
                    self._write_progress_result(
                        best_score=best_score,
                        best_values=best_values,
                        best_total_detail=best_total_detail,
                        best_img=best_img,
                        stop_reason="running",
                        history=history,
                        in_progress=True,
                    )
                else:
                    self._apply_value_map_or_recover(
                        base_values,
                        "Failed to restore base values after rejected joint phase",
                    )

            if fallback_move is not None and (
                not accepted_params_in_pass
                or float(fallback_move["score"]) + self.min_improve < best_score
            ):
                fallback_name = str(fallback_move["name"])
                fallback_value = float(fallback_move["value"])
                fallback_values = base_values.copy()
                fallback_values[fallback_name] = fallback_value
                self._apply_value_map_or_recover(
                    fallback_values,
                    f"Failed to apply fallback values for {fallback_name}",
                )
                best_score = float(fallback_move["score"])
                best_total_detail = fallback_move["total_detail"]  # type: ignore[assignment]
                best_baseline = fallback_move["baseline"]  # type: ignore[assignment]
                best_img = fallback_move["img_path"]  # type: ignore[assignment]
                best_values = fallback_values.copy()
                improved_in_iter = True
                print(
                    f"single_fallback accepted_param={fallback_name} "
                    f"best_score={best_score:.6f} "
                    f"{self._top_board_summary(best_total_detail)}"
                )
                self._write_progress_result(
                    best_score=best_score,
                    best_values=best_values,
                    best_total_detail=best_total_detail,
                    best_img=best_img,
                    stop_reason="running",
                    history=history,
                    in_progress=True,
                )

            if accepted_params_in_pass:
                joined = ",".join(accepted_params_in_pass)
                print(
                    f"joint_update accepted_params={joined} best_score={best_score:.6f} "
                    f"{self._top_board_summary(best_total_detail)}"
                )

            if best_score <= self.target_score:
                print("Target score reached.")
                break

            if not improved_in_iter and all(p.step <= p.min_step + 1e-12 for p in self.params):
                stop_reason = "all_steps_minimum"
                print("No further improvement and all steps at min_step. Stop.")
                break

        result = self._build_result_payload(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason=stop_reason,
            history=history,
            in_progress=False,
        )
        self._write_progress_result(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason=stop_reason,
            history=history,
            in_progress=False,
        )
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IPGMovie camera calibration multi-board matching loop"
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to JSON config, for example config.rear_tv.json",
    )
    parser.add_argument(
        "--inspect-controls",
        action="store_true",
        help="Only print editable controls in settings window",
    )
    parser.add_argument(
        "--capture-clicks",
        action="store_true",
        help="Interactively capture parameter click coordinates in settings window",
    )
    parser.add_argument(
        "--capture-click",
        default=None,
        help="Capture click coordinate for only one parameter name (e.g. pos_x)",
    )
    parser.add_argument(
        "--capture-initials",
        action="store_true",
        help="Read current settings values and print initial values",
    )
    parser.add_argument(
        "--write-initials-to-config",
        action="store_true",
        help="Write captured initial values back to --config file",
    )
    parser.add_argument(
        "--propose-boards",
        action="store_true",
        help="Auto-detect candidate board instances from real_image and write a proposed config",
    )
    parser.add_argument(
        "--proposal-output",
        default=None,
        help="Optional path for proposed config output",
    )
    parser.add_argument(
        "--proposal-preview",
        default=None,
        help="Optional path for proposal preview image output",
    )
    parser.add_argument(
        "--annotate-image",
        default=None,
        help="Annotate an existing simulation image using the current config",
    )
    parser.add_argument(
        "--annotate-output",
        default=None,
        help="Optional output path for --annotate-image",
    )
    parser.add_argument(
        "--resume-from-result",
        action="store_true",
        help="Resume parameter values from output_dir/result.json before optimize",
    )
    return parser.parse_args()


def main() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True, write_through=True)
        sys.stderr.reconfigure(line_buffering=True, write_through=True)
    except Exception:
        pass

    args = parse_args()
    config_path = Path(args.config).resolve()
    with open(config_path, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    base_output_dir = _resolve_config_output_dir(cfg, config_path)
    cfg["output_dir"] = str(base_output_dir)
    should_optimize = not any(
        [
            args.propose_boards,
            bool(args.annotate_image),
            args.inspect_controls,
            args.capture_clicks,
            bool(args.capture_click),
            args.capture_initials,
        ]
    )

    marker_path: Optional[Path] = None
    marker_payload: Optional[dict] = None
    if should_optimize:
        cfg["output_dir"] = str(_build_isolated_output_dir(base_output_dir.name))
        marker_path = _marker_path_for_output_dir(base_output_dir)
        marker_payload = {
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": str(config_path),
            "base_output_dir": str(base_output_dir),
            "output_dir": str(cfg["output_dir"]),
            "max_iters": int(cfg.get("max_iters", 0)),
            "resume_from_result": bool(args.resume_from_result),
            "status": "starting",
        }
        _write_run_marker(marker_path, marker_payload)

    live_log_path = _configure_live_log(cfg, args.resume_from_result)
    print("Live log:", str(live_log_path))
    if should_optimize:
        print("Isolated output dir:", str(cfg["output_dir"]))

    if marker_path is not None and marker_payload is not None:
        marker_payload["status"] = "running"
        marker_payload["live_log"] = str(live_log_path)
        _write_run_marker(marker_path, marker_payload)

    calib = CameraCalibrator(cfg)
    calib.live_log_path = live_log_path
    try:
        if args.propose_boards:
            calib.propose_boards_config(
                args.config,
                output_path=args.proposal_output,
                preview_path=args.proposal_preview,
            )
            return

        if args.annotate_image:
            annotated_path, board_scores = calib.annotate_existing_image(
                Path(args.annotate_image),
                Path(args.annotate_output) if args.annotate_output else None,
            )
            print("Annotated image:", str(annotated_path))
            for score in board_scores:
                print(
                    f"{score.board_id}: score={score.total_score:.6f} compared={score.compared} "
                    f"failed_reason={score.failed_reason}"
                )
            return

        calib.connect_windows(allow_missing_settings=args.inspect_controls)

        if args.inspect_controls:
            calib.list_edit_controls()
            return

        if args.capture_clicks or args.capture_click:
            calib.capture_click_positions(only_param=args.capture_click)
            return

        if args.capture_initials:
            values = calib.capture_initial_values()
            if args.write_initials_to_config:
                calib.write_initial_values_to_config(args.config, values)
            return

        if args.resume_from_result:
            resume_result_path = _read_latest_result_path(marker_path, base_output_dir)
            calib.load_best_values_from_result(resume_result_path)

        result = calib.optimize()
        if marker_path is not None and marker_payload is not None:
            marker_payload.update(
                {
                    "status": "finished",
                    "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "best_score": result["best_score"],
                    "best_values": result["best_values"],
                    "best_image": result["best_image"],
                    "result_json": str(Path(cfg["output_dir"]) / "result.json"),
                    "run_session_id": result.get("run_session_id"),
                }
            )
            _write_run_marker(marker_path, marker_payload)
        print("Best score:", result["best_score"])
        print("Best values:", result["best_values"])
        print("Best image:", result["best_image"])
        print("Result JSON:", str(Path(cfg["output_dir"]) / "result.json"))
    except Exception as exc:
        if marker_path is not None and marker_payload is not None:
            marker_payload.update(
                {
                    "status": "failed",
                    "failed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "error": str(exc),
                }
            )
            _write_run_marker(marker_path, marker_payload)
        raise


if __name__ == "__main__":
    main()
