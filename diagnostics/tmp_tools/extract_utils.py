"""Script to remove functions from camera_calibration.py that were moved to utils.py,
and add the import for them."""

import re

TARGET = r"C:\CM_Projects\CMO141_Calibration\Data\Script\CameraCalibration\src\calibration\camera_calibration.py"

with open(TARGET, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Functions to remove — matched by their exact definition line (def <name>)
FUNCTIONS_TO_REMOVE = [
    "_unlink_if_exists",
    "_default_sim_output_root",
    "_sim_output_root_legacy",
    "_deep_merge_dict",
    "_path_to_json_string",
    "_bootstrap_partial_template_dir",
    "_is_custom_marker_board_type",
    "_is_aruco_family_board_type",
    "_is_apriltag_board_type",
    "_is_circle_grid_board_type",
    "_is_aruco_grid_board_type",
    "_derive_camera_name_from_image_path",
    "_board_prototype_family",
    "_canonical_camera_group_name",
    "_camera_name_from_output_dir",
    "_quantize_float",
    "_format_scalar_value_map",
    "_clamp_to_parameter_bounds",
    "_resolve_parameter_bounds",
    "_build_explicit_parameter_config",
]

# Also remove the _DEFAULT_BOUNDS_MULTIPLIER constant (moved to utils.py)
CONSTANTS_TO_REMOVE = [
    "_DEFAULT_BOUNDS_MULTIPLIER",
]


def find_function_span(name, lines):
    """Find the start and end line (0-indexed) of a module-level function definition."""
    pattern = re.compile(rf"^def {re.escape(name)}\(")
    start = None
    for i, line in enumerate(lines):
        if pattern.match(line):
            start = i
            break
    if start is None:
        return None, None
    # Find end: next non-blank line at module level (not indented) after function
    # A function ends at the next `def ` or `class ` at column 0
    end = len(lines) - 1
    for i in range(start + 1, len(lines)):
        stripped = lines[i].rstrip("\n")
        if stripped == "":
            continue
        # If we hit another top-level def or class, the previous function ends
        if re.match(r"^(def |class |@)", stripped):
            # Go back through blank lines to find the actual end
            j = i - 1
            while j > start and lines[j].strip() == "":
                j -= 1
            end = j
            break
    return start, end


def find_constant_span(name, lines):
    """Find a module-level constant assignment line."""
    pattern = re.compile(rf"^{re.escape(name)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i, i
    return None, None


# Collect all spans to remove
spans_to_remove = []
for name in FUNCTIONS_TO_REMOVE:
    s, e = find_function_span(name, lines)
    if s is not None:
        # Include preceding blank lines (up to 2) to keep formatting clean
        while s > 0 and lines[s - 1].strip() == "":
            s -= 1
        spans_to_remove.append((s, e))
        print(f"  Found {name}: lines {s+1}-{e+1} (removing)")
    else:
        print(f"  WARNING: {name} not found as module-level function")

for name in CONSTANTS_TO_REMOVE:
    s, e = find_constant_span(name, lines)
    if s is not None:
        spans_to_remove.append((s, e))
        print(f"  Found {name}: line {s+1} (removing)")
    else:
        print(f"  NOTE: {name} not found (may already be removed)")

# Sort by start descending so removal doesn't shift later indices
spans_to_remove.sort(key=lambda x: x[0], reverse=True)

# Remove
for s, e in spans_to_remove:
    del lines[s:e + 1]

print(f"\nRemoved {len(spans_to_remove)} spans, {sum(e - s + 1 for s, e in spans_to_remove)} lines total")

# Add the utils import after the calib_types import
import_idx = None
for i, line in enumerate(lines):
    if "from src.calibration.calib_types import" in line:
        import_idx = i
        break

if import_idx is not None:
    utils_import = "from src.calibration.utils import (\n"
    for name in FUNCTIONS_TO_REMOVE:
        utils_import += f"    {name},\n"
    utils_import += "    _build_annotation_legend_lines,\n"
    utils_import += ")\n"
    # Insert after the calib_types import line
    lines.insert(import_idx + 1, utils_import)
    print(f"Added utils import after line {import_idx + 1}")

with open(TARGET, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Done.")
