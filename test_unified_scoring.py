#!/usr/bin/env python
"""Test unified scoring display."""

import sys
sys.path.insert(0, '.')

from src.calibration.calib_types import BoardScoreDetail, BoardProfile, TotalScoreDetail


def test_unified_scoring():
    """Test that board scores are displayed with unified standard."""

    # Create mock boards with weights
    boards = [
        BoardProfile(
            board_id='mk_1',
            board_type='custom_maker',
            weight=0.8,
            critical=True,
            roi=None,
        ),
    ]

    # Create total detail with different board score vs total score
    total_detail = TotalScoreDetail(
        success=True,
        total_score=20.38,  # This is the weighted average (what user sees as total)
        raw_total_score=20.38,
        degrade_penalty=0.0,
        has_critical_degrade=False,
        degraded_boards=[],
        isolated_outlier_boards=[],
        compared_board_count=1,
        board_scores=[
            BoardScoreDetail(
                board_id='mk_1',
                board_type='custom_maker',
                success=True,
                compared=True,
                reference_visible=True,
                sim_visible=True,
                total_score=25.47,  # This is the raw board score (should NOT be shown)
                rmse=23.15,
                mean_error=23.15,
                max_error=23.15,
                miss_rate=0.0,
                matched_point_count=9,
            ),
        ],
    )

    # Test the function
    from src.calibration.utils import _build_annotation_legend_lines

    # With boards provided (should show unified standard)
    lines_with_boards = _build_annotation_legend_lines(total_detail, boards=boards)

    # Without boards (should show raw score)
    lines_without_boards = _build_annotation_legend_lines(total_detail)

    print("=== Test Unified Scoring Display ===")
    print(f"Total score (what user sees): {total_detail.total_score:.3f}")
    print(f"Raw board score (should NOT be shown): {total_detail.board_scores[0].total_score:.3f}")
    print()
    print("With boards (unified standard):")
    for line in lines_with_boards:
        print(f"  {line}")
    print()
    print("Without boards (fallback to raw):")
    for line in lines_without_boards:
        print(f"  {line}")

    # Verify that with boards, the displayed score matches the total
    assert "20.380" in lines_with_boards[0], f"Expected unified score 20.380, got {lines_with_boards[0]}"
    print("\n[PASS] Unified scoring test passed!")

    # Verify that without boards, it shows the raw score
    assert "25.470" in lines_without_boards[0], f"Expected raw score 25.470, got {lines_without_boards[0]}"
    print("[PASS] Fallback to raw score test passed!")

    return True


if __name__ == '__main__':
    try:
        test_unified_scoring()
        print("\nAll tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
