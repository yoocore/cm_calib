"""Phase 3: extract config functions from camera_calibration.py to config.py"""

import re
from pathlib import Path

TARGET = r"C:\CM_Projects\CMO141_Calibration\Data\Script\CameraCalibration\src\calibration\camera_calibration.py"
OUTPUT = r"C:\CM_Projects\CMO141_Calibration\Data\Script\CameraCalibration\src\calibration\config.py"

with open(TARGET, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Functions to move to config.py
CONFIG_FUNCTIONS = [
    "_default_bootstrap_template_path",
    "_default_parameter_config",
    "_default_parameter_order",
    "_default_bootstrap_config",
    "_resolved_bootstrap_config",
    "_preprocess_auto_template_match_image",
    "_masked_secondary_response_max",
    "_select_auto_template_crop",
    "_materialize_auto_template_image",
    "_get_annotation_ocr_engine",
    "_normalize_annotation_board_id",
    "_run_annotation_ocr",
    "_rect_gap_distance",
    "_assign_annotation_board_ids",
    "_extract_annotation_board_ids",
    "_extract_annotation_rectangles",
    "_cluster_1d",
    "_group_annotation_rectangles",
    "_load_bootstrap_template_specs",
    "_build_boards_from_annotation_rectangles",
    "_auto_upgrade_partial_checkerboards",
    "_sync_materialized_board_fields_from_calibrator",
    "bootstrap_config_from_annotation",
]

CONFIG_CONSTANTS = [
    "_ANNOTATION_OCR_ENGINE",
]


def find_definition_span(name, lines):
    """Find start/end (0-indexed) of a module-level name definition."""
    patterns = [
        re.compile(rf"^def {re.escape(name)}\("),
        re.compile(rf"^{re.escape(name)}\s*="),
    ]
    start = None
    for i, line in enumerate(lines):
        for pat in patterns:
            if pat.match(line):
                start = i
                break
        if start is not None:
            break
    if start is None:
        return None, None

    # If it's a constant (not 'def'), it's just one line
    if not re.match(r"^\s*def\s", lines[start]):
        return start, start

    # Find end of function
    end = len(lines) - 1
    for i in range(start + 1, len(lines)):
        stripped = lines[i].rstrip("\n")
        if stripped == "":
            continue
        if re.match(r"^(def |class |@)", stripped):
            j = i - 1
            while j > start and lines[j].strip() == "":
                j -= 1
            end = j
            break
    return start, end


# Collect spans
spans = []
for name in CONFIG_FUNCTIONS + CONFIG_CONSTANTS:
    s, e = find_definition_span(name, lines)
    if s is not None:
        # Include preceding blank lines
        while s > 0 and lines[s - 1].strip() == "":
            s -= 1
        spans.append((name, s, e))
        print(f"  Found {name}: lines {s+1}-{e+1} ({e-s+1} lines)")
    else:
        print(f"  WARNING: {name} not found")

# Sort by line ascending for writing config.py
spans_sorted = sorted(spans, key=lambda x: x[1])

# Write config.py
config_lines = [
    '"""Configuration bootstrap, template handling, and annotation OCR for camera calibration."""\n',
    "\n",
    "import copy\n",
    "import json\n",
    "import re\n",
    "from pathlib import Path\n",
    "from typing import Dict, List, Optional, Tuple\n",
    "\n",
    "import cv2\n",
    "import numpy as np\n",
    "\n",
    "from src.calibration.calib_types import ParameterSpec, BoardProfile\n",
    "from src.calibration.utils import (\n",
    "    _board_prototype_family,\n",
    "    _bootstrap_partial_template_dir,\n",
    "    _derive_camera_name_from_image_path,\n",
    "    _deep_merge_dict,\n",
    "    _is_aruco_family_board_type,\n",
    "    _is_aruco_grid_board_type,\n",
    "    _is_apriltag_board_type,\n",
    "    _is_circle_grid_board_type,\n",
    "    _is_custom_marker_board_type,\n",
    ")\n",
    "\n",
    "\n",
]

for name, s, e in spans_sorted:
    for i in range(s, e + 1):
        config_lines.append(lines[i])

# Make sure file ends with newline
if config_lines and not config_lines[-1].endswith("\n"):
    config_lines.append("\n")

with open(OUTPUT, "w", encoding="utf-8") as f:
    f.writelines(config_lines)

print(f"\nWrote {len(config_lines)} lines to config.py")

# Now remove from camera_calibration.py (bottom-up)
spans_to_remove = [(s, e) for _, s, e in spans]
spans_to_remove.sort(key=lambda x: x[0], reverse=True)
for s, e in spans_to_remove:
    del lines[s:e + 1]

print(f"Removed {len(spans_to_remove)} spans from camera_calibration.py")

# Add import
import_idx = None
for i, line in enumerate(lines):
    if "from src.calibration.utils import" in line:
        import_idx = i
        break

if import_idx is not None:
    # Insert after the utils import block (after the closing paren)
    # Find the closing paren
    j = import_idx
    while j < len(lines) and ")" not in lines[j]:
        j += 1
    # j is the line with the closing paren
    config_import = "from src.calibration.config import (\n"
    for name in CONFIG_FUNCTIONS:
        config_import += f"    {name},\n"
    config_import += ")\n"
    lines.insert(j + 1, config_import)
    print(f"Added config import after line {j + 1}")

with open(TARGET, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Done.")
