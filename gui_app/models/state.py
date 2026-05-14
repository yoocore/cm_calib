from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Optional


class AppStatus(StrEnum):
    IDLE = "idle"
    PREPARING = "preparing"
    PASSIVE = "passive"
    READY = "ready"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(slots=True)
class CameraResult:
    camera: str
    best_score: Optional[float] = None
    current_iter_score: Optional[float] = None
    current_iter_index: Optional[int] = None
    live_log: Optional[str] = None
    result_json: Optional[str] = None
    best_image: Optional[str] = None
    best_score_image: Optional[str] = None
    best_overlay_image: Optional[str] = None
    status: str = "pending"
    error: Optional[str] = None


@dataclass(slots=True)
class CalibrationLaunchConfig:
    project_root: Path
    testrun: str
    cameras: list[str]
    campaign_rounds: int = 1
    multi_start_count: int = 0
    multi_start_iters: Optional[int] = None
    multi_start_jitter_steps: float = 2.0
    multi_start_seed: int = 20260429
    explore_then_refine: bool = False
    refine_iters: Optional[int] = None
    resume_from_result: bool = False
    output_dir: Optional[Path] = None
    skip_prepare_for_first_camera: bool = False


@dataclass(slots=True)
class ApplicationState:
    status: AppStatus = AppStatus.IDLE
    output_dir: Optional[Path] = None
    selected_cameras: list[str] = field(default_factory=list)
