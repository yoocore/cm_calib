"""Standalone script: compare calibration round scores using current scoring system.

Usage:
    python scripts/compare_round_scores.py

Requires the CarMaker project directory structure at C:\CM_Projects\TM15.1_StreamaxCamera.
"""
import sys
import json
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
from PIL import Image

# Add project src to Python path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.calib_types import BoardProfile, DetectionResult, EvalImageTransform
from src.calibration.detector import DetectorMixin
from src.calibration.scoring import ScoringMixin
from src.calibration.utils import _is_custom_marker_board_type
from src.calibration.config import _select_auto_template_crop


# ---------- paths ----------
CM_PROJECT = Path(r"C:\CM_Projects\TM15.1_StreamaxCamera")

CONFIG_PATH = CM_PROJECT / "Movie" / "calibtool_VehSensor_0" / "camera.VehSensor_0.json"
REAL_IMG_PATH = CM_PROJECT / "Movie" / "calibtool_VehSensor_0" / "frame_000001_raw.png.png"

R1_BEST = (
    CM_PROJECT
    / "SimOutput" / "calibration" / "VehSensor_0"
    / "rounds_20260630_134512" / "round_01" / "campaign" / "explore" / "start_00"
    / "iter_0001_yaw_p.png"
)
R2_BEST = (
    CM_PROJECT
    / "SimOutput" / "calibration" / "VehSensor_0"
    / "rounds_20260629_162520" / "round_01" / "campaign" / "explore" / "start_00"
    / "iter_0003_pitch_p.png"
)
R1_RESULT = R1_BEST.with_name("result.json")
R2_RESULT = R2_BEST.with_name("result.json")


# ---------- helper: load templates ( replicates CameraCalibrator._load_custom_templates ) ----------
def load_custom_templates(boards: List[BoardProfile], template_feature_max_dim: int = 2048) -> Dict[str, dict]:
    """Load template data for each board.  Returns dict[board_id] -> template_info."""
    templates: Dict[str, dict] = {}
    for board in boards:
        if not board.template_image:
            continue
        tpl_path = Path(board.template_image)
        if not tpl_path.exists():
            raise FileNotFoundError(f"Template not found: {tpl_path}")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(str(tpl_path)) as pil_img:
                if pil_img.mode == "P" and "transparency" in pil_img.info:
                    pil_img = pil_img.convert("RGBA")
                template_gray = np.ascontiguousarray(np.array(pil_img.convert("L"), dtype=np.uint8))

        # apply template_crop if set
        if board.template_crop is not None:
            cx, cy, cw, ch = board.template_crop
            cx1 = cx + cw
            cy1 = cy + ch
            if cx1 > template_gray.shape[1] or cy1 > template_gray.shape[0]:
                raise ValueError(f"template_crop outside image for {board.board_id}")
            template_gray = template_gray[cy:cy1, cx:cx1]

        # for feature-based detectors (not template_match), resize large templates
        if board.custom_detector == "feature" and board.board_type != "checkerboard":
            max_dim = max(template_gray.shape[:2])
            if max_dim > template_feature_max_dim:
                scale = template_feature_max_dim / max_dim
                new_w = max(1, int(round(template_gray.shape[1] * scale)))
                new_h = max(1, int(round(template_gray.shape[0] * scale)))
                template_gray = cv2.resize(template_gray, (new_w, new_h), interpolation=cv2.INTER_AREA)

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
        template_info: dict = {
            "template": template_gray,
            "anchors": anchor_points,
        }

        if _is_custom_marker_board_type(board.board_type):
            match_template, match_crop = _select_auto_template_crop(
                template_gray, int(board.template_binary_threshold)
            )
            template_info["match_template"] = match_template
            template_info["match_crop"] = match_crop
            # content_bbox via OTSU
            _, content_mask = cv2.threshold(
                template_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
            )
            content_points = np.column_stack(np.where(content_mask > 0))
            if content_points.size > 0:
                min_y = int(np.min(content_points[:, 0]))
                max_y = int(np.max(content_points[:, 0]))
                min_x = int(np.min(content_points[:, 1]))
                max_x = int(np.max(content_points[:, 1]))
                template_info["content_bbox"] = (
                    min_x, min_y, max_x - min_x + 1, max_y - min_y + 1,
                )

        templates[board.board_id] = template_info
    return templates


# ---------- combined scorer ----------
class StandaloneScorer(DetectorMixin, ScoringMixin):
    """Minimal combined class for running detection + scoring outside CameraCalibrator."""

    def __init__(
        self,
        real_img: np.ndarray,
        boards: List[BoardProfile],
        custom_templates: Dict[str, dict],
    ):
        self.real_img = real_img
        self.boards = boards
        self.custom_templates = custom_templates
        self.orb = cv2.ORB_create(nfeatures=3000)
        self._sim_sourced_board_ids: Set[str] = set()
        self.compare_only_if_reference_visible = False
        self.keep_aspect_resize = True
        self.template_feature_max_dim = 2048


# ---------- helpers ----------
def parse_board_profile(bc: dict) -> BoardProfile:
    return BoardProfile(
        board_id=bc["board_id"],
        board_type=bc["board_type"],
        weight=float(bc.get("weight", 1.0)),
        critical=bool(bc.get("critical", False)),
        roi=None if bc.get("roi") is None else tuple(int(v) for v in bc["roi"]),
        custom_detector=str(bc.get("custom_detector", "feature")),
        template_match_threshold=float(bc.get("template_match_threshold", 0.0)),
        template_binary_threshold=int(bc.get("template_binary_threshold", 0)),
        min_detected_points=int(bc.get("min_detected_points", 1)),
        template_image=str(bc["template_image"]) if bc.get("template_image") else None,
        template_source_roi=None if bc.get("template_source_roi") is None
        else tuple(int(v) for v in bc["template_source_roi"]),
        template_source_crop=None if bc.get("template_source_crop") is None
        else tuple(int(v) for v in bc["template_source_crop"]),
        alpha=float(bc.get("alpha", 1000.0)),
        beta=float(bc.get("beta", 0.1)),
        fail_penalty=float(bc.get("fail_penalty", 1e6)),
        min_match_count=int(bc.get("min_match_count", 20)),
        detect_roi_padding=int(bc.get("detect_roi_padding", 0)),
        template_crop=None if bc.get("template_crop") is None
        else tuple(int(v) for v in bc["template_crop"]),
    )


def score_round(
    scorer: StandaloneScorer,
    board: BoardProfile,
    sim_img_path: Path,
) -> dict:
    """Run detection and scoring for one round. Returns dict of results."""
    h_real, w_real = scorer.real_img.shape[:2]
    sim_eval = cv2.imread(str(sim_img_path), cv2.IMREAD_GRAYSCALE)
    if sim_eval is None:
        return {"error": f"Cannot read {sim_img_path}"}
    # Resize sim to match real image dimensions for fair comparison
    if sim_eval.shape[:2] != (h_real, w_real):
        print(f"    (resizing sim {sim_eval.shape} -> ({h_real}, {w_real}))")
        sim_eval = cv2.resize(sim_eval, (w_real, h_real), interpolation=cv2.INTER_NEAREST)

    # --- reference (real) detection ---
    real_detection = scorer._reference_detection_from_board_geometry(board)
    if real_detection is None:
        real_detection = scorer._detect_board(scorer.real_img, board)

    # --- sim detection via template_match ---
    sim_detection = scorer._detect_template_match_board(sim_eval, board)

    # --- score ---
    board_score = scorer._score_board(board, real_detection, sim_detection, sim_eval_image=sim_eval)

    return {
        "real_visible": real_detection.success,
        "sim_visible": sim_detection.success,
        "real_points": int(real_detection.point_count) if real_detection.success else 0,
        "sim_points": int(sim_detection.point_count) if sim_detection.success else 0,
        "total_score": board_score.total_score,
        "rmse": board_score.rmse,
        "mean_error": board_score.mean_error,
        "max_error": board_score.max_error,
        "miss_rate": board_score.miss_rate,
        "matched_points": board_score.matched_point_count,
        "compared": board_score.compared,
        "failed_reason": board_score.failed_reason,
    }


# ---------- main ----------
def main():
    print("=" * 72)
    print("  Calibration Round Score Comparison (current scoring system)")
    print("=" * 72)

    # 1. load config
    print(f"\n[1] Loading config: {CONFIG_PATH}")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    boards = [parse_board_profile(bc) for bc in cfg["boards"]]
    for b in boards:
        print(f"    board: {b.board_id}, type={b.board_type}, roi={b.roi}, "
              f"threshold={b.template_match_threshold}, binary_thresh={b.template_binary_threshold}")

    # 2. load real image
    print(f"\n[2] Loading real image: {REAL_IMG_PATH}")
    real_img = cv2.imread(str(REAL_IMG_PATH), cv2.IMREAD_GRAYSCALE)
    if real_img is None:
        print("    ERROR: cannot read real image")
        sys.exit(1)
    print(f"    shape: {real_img.shape}")

    # 3. load templates
    print(f"\n[3] Loading templates")
    custom_templates = load_custom_templates(boards)
    for bid, info in custom_templates.items():
        tpl = info["template"]
        has_match = info.get("match_template") is not None
        print(f"    {bid}: template shape={tpl.shape}, has_match={has_match}, "
              f"content_bbox={info.get('content_bbox')}")

    # 4. create scorer
    print(f"\n[4] Creating scorer")
    scorer = StandaloneScorer(real_img, boards, custom_templates)

    # 5. score each round
    rounds = [
        ("R1 (2026-06-30)", R1_BEST, R1_RESULT),
        ("R2 (2026-06-29)", R2_BEST, R2_RESULT),
    ]

    for label, sim_path, result_path in rounds:
        print(f"\n[{label}]")
        print(f"    sim image: {sim_path}")

        if not sim_path.exists():
            print(f"    SKIP: file not found")
            continue

        result = score_round(scorer, boards[0], sim_path)

        if "error" in result:
            print(f"    ERROR: {result['error']}")
            continue

        # load old result for comparison
        old = {}
        if result_path.exists():
            with open(result_path) as f:
                old_data = json.load(f)
            bm = old_data.get("best_metrics", {})
            bs_list = bm.get("board_scores", [])
            if bs_list:
                bs0 = bs_list[0]
                old = {
                    "old_total_score": bs0.get("score"),
                    "old_rmse": bs0.get("rmse"),
                    "old_mean_error": bs0.get("mean_error"),
                    "old_max_error": bs0.get("max_error"),
                    "old_miss_rate": bs0.get("miss_rate"),
                }

        print(f"\n    ---- Detection ----")
        print(f"    Real visible: {result['real_visible']} ({result['real_points']} pts)")
        print(f"    Sim  visible: {result['sim_visible']} ({result['sim_points']} pts)")
        print(f"    Matched: {result['matched_points']} pts, miss_rate={result['miss_rate']:.4f}")

        print(f"\n    ---- Scores ----")
        if old:
            print(f"    {'Metric':<20} {'Current':>10} {'Old (result.json)':>18}")
            print(f"    {'-'*50}")
            m = [
                ("total_score", f"{result['total_score']:.2f}", f"{old.get('old_total_score', 'N/A')}"),
                ("rmse",        f"{result['rmse']:.2f}",        f"{old.get('old_rmse', 'N/A')}"),
                ("mean_error",  f"{result['mean_error']:.2f}",  f"{old.get('old_mean_error', 'N/A')}"),
                ("max_error",   f"{result['max_error']:.2f}",   f"{old.get('old_max_error', 'N/A')}"),
            ]
            for name, cur, prev in m:
                print(f"    {name:<20} {cur:>10}  {prev:>18}")
        else:
            print(f"    total_score = {result['total_score']:.2f}")
            print(f"    rmse        = {result['rmse']:.2f}")
            print(f"    mean_error  = {result['mean_error']:.2f}")
            print(f"    max_error   = {result['max_error']:.2f}")

        # Note: geometric_penalty is embedded in rmse (additive), not separately recoverable here.
        if result.get("failed_reason"):
            print(f"    NOTE: {result['failed_reason']}")

    print("\n" + "=" * 72)
    print("  Done.")
    print("=" * 72)


if __name__ == "__main__":
    main()
