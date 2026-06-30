from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class ParameterSpec:
    name: str
    value: float
    step: float
    min_value: float
    max_value: float
    min_step: float
    decimals: int


@dataclass
class BoardProfile:
    board_id: str
    board_type: str
    weight: float
    critical: bool
    roi: Optional[Tuple[int, int, int, int]]
    detect_roi_padding: int = 0
    template_source_roi: Optional[Tuple[int, int, int, int]] = None
    template_source_crop: Optional[Tuple[int, int, int, int]] = None
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
    aruco_dictionary: str = "DICT_4X4_50"
    marker_length_ratio: float = 0.7
    tag_family: str = "tagStandard41h12"
    grid_type: str = "symmetric"
    marker_separation: float = 0.0


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
    match_score: Optional[float] = None


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
    raw_total_score: float
    degrade_penalty: float
    has_critical_degrade: bool
    degraded_boards: List[str]
    isolated_outlier_boards: List[str]
    compared_board_count: int
    board_scores: List[BoardScoreDetail]
    failed_reason: Optional[str] = None


@dataclass
class AcceptanceDecision:
    passed: bool
    mode: str
    reason: str
    target_score_reached: bool
    compared_board_count: int
    max_board_score: Optional[float]
    avg_board_score: Optional[float]


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
