"""Standalone FBO capture + score evaluation against reference image."""

import json
import sys
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "Data" / "Script" / "CameraCalibration"))

from dde_health_check import render_dde_execute_script, run_check_attempt

CONFIG_PATH = Path("C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/configs/camera.right_rear.json")
OUTPUT_DIR = Path("C:/CM_Projects/CMO141_Calibration/SimOutput/fbo_score_check")


DDE_SERVICE = "TclEval"
DDE_TOPIC = "CarMaker"


def query_movie_state() -> dict:
    """Query current IPG-MOVIE view state via DDE through CarMaker."""
    output_dir = OUTPUT_DIR / "query"
    output_dir.mkdir(parents=True, exist_ok=True)

    script_text = render_dde_execute_script(
        output_dir / "query_state.txt",
        "IPG-MOVIE",
        [
            "scan $View(ev.view) %d vno",
            'set wpath ".view$vno"',
            "set wi [$wpath.gl0 cget -width]",
            "set he [$wpath.gl0 cget -height]",
            'puts "view_widget=$wpath"',
            'puts "width=$wi"',
            'puts "height=$he"',
            'puts "ev_view=$View(ev.view)"',
            "catch {set cam [.camera.cammod cget -camera]}",
            'puts "camera=$cam"',
            "catch {set sel [.camera.camlist curselection]}",
            'puts "camlist_selection=$sel"',
        ],
    )

    result = run_check_attempt(
        "query_state",
        DDE_SERVICE,
        DDE_TOPIC,
        output_dir,
        script_text,
        timeout_sec=5.0,
    )
    return result


def _build_capture_body_lines(capture_path: Path, *, stage: str) -> list[str]:
    body_lines = [
        "scan $View(ev.view) %d vno",
        'set wpath ".view$vno"',
        "set wi [$wpath.gl0 cget -width]",
        "set he [$wpath.gl0 cget -height]",
        "set captureFBO [FBO new $wi $he -tex rgb -noclear]",
    ]
    if stage == "new":
        body_lines.extend([
            'puts "stage=new;status=ok"',
            "catch {FBO delete $captureFBO}",
        ])
        return body_lines
    body_lines.extend([
        "set update_rc [catch {",
        "    FBO begin $captureFBO",
    ])
    if stage in {"update", "readpixels"}:
        body_lines.append("    UpdateView $vno")
    body_lines.extend([
        "    FBO end",
        "} update_msg]",
        "catch {FBO end}",
        "if {$update_rc != 0} {",
        "    catch {FBO delete $captureFBO}",
        "    error $update_msg",
        "}",
    ])
    if stage == "begin_end":
        body_lines.extend([
            'puts "stage=begin_end;status=ok"',
            "catch {FBO delete $captureFBO}",
        ])
        return body_lines
    if stage not in {"update", "readpixels"}:
        raise ValueError(f"Unsupported FBO capture stage: {stage}")
    if stage == "update":
        body_lines.extend([
            'puts "stage=update;status=ok"',
            "catch {FBO delete $captureFBO}",
        ])
        return body_lines
    body_lines.extend([
        "catch {image delete probeImg}",
        "image create photo probeImg -width $wi -height $he",
        "gl bindframebuffer_read $captureFBO",
        "gl readpixels 0 0 probeImg",
        f'probeImg write "{capture_path.as_posix()}" -format png',
        "catch {gl bindframebuffer_read 0}",
        "catch {FBO delete $captureFBO}",
        'puts "captured=${wi}x${he}"',
    ])
    return body_lines


def capture_fbo(output_dir: Path, *, stage: str = "readpixels") -> Path:
    """Capture or probe FBO stages from current IPG-MOVIE view via CarMaker DDE."""
    output_dir.mkdir(parents=True, exist_ok=True)
    capture_path = output_dir / "fbo_capture.png"
    result_path = output_dir / f"fbo_capture_{stage}.txt"

    script_text = render_dde_execute_script(
        result_path,
        "IPG-MOVIE",
        _build_capture_body_lines(capture_path, stage=stage),
    )

    result = run_check_attempt(
        f"fbo_capture_{stage}",
        DDE_SERVICE,
        DDE_TOPIC,
        output_dir,
        script_text,
        timeout_sec=10.0,
    )

    if result.get("rc") != 0:
        raise RuntimeError(f"FBO capture failed: {result}")

    if stage == "readpixels" and not capture_path.exists():
        raise RuntimeError(f"Capture file not created: {capture_path}")

    return capture_path


def detect_checkerboard(img: np.ndarray, board_size: tuple, roi: tuple) -> np.ndarray:
    """Detect checkerboard corners in image within ROI."""
    x, y, w, h = roi
    roi_img = img[y:y+h, x:x+w]

    # Convert to grayscale if needed
    if len(roi_img.shape) == 3:
        roi_gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    else:
        roi_gray = roi_img

    found, corners = cv2.findChessboardCorners(
        roi_gray,
        board_size,
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE,
    )

    if found:
        # Adjust corners to full image coordinates
        corners[:, 0, 0] += x
        corners[:, 0, 1] += y
        return corners
    return None


def detect_custom_marker(img: np.ndarray, template_path: str, roi: tuple, threshold: float = 0.55) -> np.ndarray:
    """Detect custom marker using template matching."""
    x, y, w, h = roi
    roi_img = img[y:y+h, x:x+w]

    template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template is None:
        return None

    if len(roi_img.shape) == 3:
        roi_gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    else:
        roi_gray = roi_img

    result = cv2.matchTemplate(roi_gray, template, cv2.TM_CCOEFF_NORMED)
    loc = np.where(result >= threshold)

    if len(loc[0]) == 0:
        return None

    # Return matched points as corners
    points = []
    for pt_y, pt_x in zip(loc[0], loc[1]):
        points.append([pt_x + x, pt_y + y])

    if len(points) < 9:
        return None

    return np.array(points[:9], dtype=np.float32).reshape(-1, 1, 2)


def compute_board_score(real_corners, sim_corners) -> float:
    """Compute RMSE-based score between real and sim corner positions."""
    if real_corners is None or sim_corners is None:
        return 999.0

    n = min(len(real_corners), len(sim_corners))
    if n == 0:
        return 999.0

    real_pts = real_corners[:n, 0, :].astype(np.float64)
    sim_pts = sim_corners[:n, 0, :].astype(np.float64)

    errors = np.sqrt(np.sum((real_pts - sim_pts) ** 2, axis=1))
    rmse = np.sqrt(np.mean(errors ** 2))
    return float(rmse)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load config
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)

    reference_path = cfg["real_image"]
    boards = cfg["boards"]

    print(f"Reference image: {reference_path}")
    print(f"Board count: {len(boards)}")

    # Check reference image
    ref_img = cv2.imread(reference_path, cv2.IMREAD_GRAYSCALE)
    if ref_img is None:
        print(f"ERROR: Cannot read reference image: {reference_path}")
        return
    print(f"Reference image size: {ref_img.shape[1]}x{ref_img.shape[0]}")

    # Query current state
    print("\n--- Querying IPG-MOVIE state ---")
    state = query_movie_state()
    msg = state.get("msg", "")
    print(msg)

    # Capture FBO
    print("\n--- Capturing FBO ---")
    try:
        capture_path = capture_fbo(OUTPUT_DIR)
        print(f"Captured: {capture_path}")
    except Exception as e:
        print(f"FBO capture failed: {e}")
        return

    sim_img = cv2.imread(str(capture_path), cv2.IMREAD_GRAYSCALE)
    if sim_img is None:
        print(f"ERROR: Cannot read captured image: {capture_path}")
        return
    print(f"Captured image size: {sim_img.shape[1]}x{sim_img.shape[0]}")

    # Resize sim image to match reference size (same as calibration code)
    ref_h, ref_w = ref_img.shape[:2]
    sim_h, sim_w = sim_img.shape[:2]
    if sim_w != ref_w or sim_h != ref_h:
        scale = min(ref_w / sim_w, ref_h / sim_h)
        new_w = int(round(sim_w * scale))
        new_h = int(round(sim_h * scale))
        resized = cv2.resize(sim_img, (new_w, new_h))
        canvas = np.zeros((ref_h, ref_w), dtype=resized.dtype)
        off_x = (ref_w - new_w) // 2
        off_y = (ref_h - new_h) // 2
        canvas[off_y:off_y + new_h, off_x:off_x + new_w] = resized
        sim_img = canvas
        print(f"Resized sim image to: {sim_img.shape[1]}x{sim_img.shape[0]}")

    # Detect boards in reference image
    print("\n--- Detecting boards in reference image ---")
    ref_detections = {}
    for board in boards:
        board_id = board["board_id"]
        roi = tuple(board["roi"])

        if board["board_type"] == "checkerboard":
            board_size = tuple(board["board_size"])
            corners = detect_checkerboard(ref_img, board_size, roi)
        elif board["board_type"] == "custom_maker":
            corners = detect_custom_marker(
                ref_img,
                board["template_image"],
                roi,
                board.get("template_match_threshold", 0.55),
            )
        else:
            corners = None

        status = "OK" if corners is not None else "NOT FOUND"
        count = len(corners) if corners is not None else 0
        print(f"  {board_id}: {status} ({count} points)")
        ref_detections[board_id] = corners

    # Detect boards in sim image (now same size as reference)
    print("\n--- Detecting boards in sim image ---")
    sim_detections = {}
    for board in boards:
        board_id = board["board_id"]
        roi = tuple(board["roi"])

        if board["board_type"] == "checkerboard":
            board_size = tuple(board["board_size"])
            corners = detect_checkerboard(sim_img, board_size, roi)
        elif board["board_type"] == "custom_maker":
            corners = detect_custom_marker(
                sim_img,
                board["template_image"],
                roi,
                board.get("template_match_threshold", 0.55),
            )
        else:
            corners = None

        status = "OK" if corners is not None else "NOT FOUND"
        count = len(corners) if corners is not None else 0
        print(f"  {board_id}: {status} ({count} points)")
        sim_detections[board_id] = corners

    # Compute scores
    print("\n--- Board Scores ---")
    total_score = 0.0
    scored_boards = 0
    for board in boards:
        board_id = board["board_id"]
        weight = board.get("weight", 1.0)
        ref_corners = ref_detections.get(board_id)
        sim_corners = sim_detections.get(board_id)

        score = compute_board_score(ref_corners, sim_corners)
        weighted_score = score * weight
        total_score += weighted_score
        scored_boards += 1

        print(f"  {board_id}: score={score:.4f}, weight={weight}, weighted={weighted_score:.4f}")

    print(f"\n  Total score: {total_score:.4f}")
    print(f"  Boards scored: {scored_boards}")

    # Save summary
    summary = {
        "reference_image": reference_path,
        "capture_image": str(capture_path),
        "state": msg,
        "total_score": total_score,
        "board_scores": {},
    }
    for board in boards:
        board_id = board["board_id"]
        ref_corners = ref_detections.get(board_id)
        sim_corners = sim_detections.get(board_id)
        summary["board_scores"][board_id] = compute_board_score(ref_corners, sim_corners)

    summary_path = OUTPUT_DIR / "score_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved: {summary_path}")


if __name__ == "__main__":
    main()
