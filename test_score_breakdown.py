#!/usr/bin/env python
"""Test score_breakdown and score_statistics functionality."""

import sys
sys.path.insert(0, '.')

from src.calibration.calib_types import BoardScoreDetail, TotalScoreDetail


def test_build_score_statistics():
    """Test _build_score_statistics method."""
    from src.calibration.orchestration import OrchestrationMixin

    class MockCalibrator:
        pass

    mixin = OrchestrationMixin()
    mixin.__class__ = type('MockOrchestrationMixin', (OrchestrationMixin, MockCalibrator), {})

    # Create test data
    board_scores = [
        BoardScoreDetail(
            board_id='mk_1',
            board_type='checkerboard',
            success=True,
            compared=True,
            reference_visible=True,
            sim_visible=True,
            total_score=25.0,
            rmse=1.5,
            mean_error=1.2,
            max_error=3.5,
            miss_rate=0.0,
            matched_point_count=30,
        ),
        BoardScoreDetail(
            board_id='mk_2',
            board_type='checkerboard',
            success=True,
            compared=True,
            reference_visible=True,
            sim_visible=True,
            total_score=15.0,
            rmse=2.5,
            mean_error=2.0,
            max_error=5.5,
            miss_rate=0.05,
            matched_point_count=28,
        ),
        BoardScoreDetail(
            board_id='mk_3',
            board_type='checkerboard',
            success=True,
            compared=True,
            reference_visible=True,
            sim_visible=True,
            total_score=18.0,
            rmse=1.8,
            mean_error=1.5,
            max_error=4.0,
            miss_rate=0.02,
            matched_point_count=29,
        ),
    ]

    total_detail = TotalScoreDetail(
        success=True,
        total_score=19.8,
        raw_total_score=18.5,
        degrade_penalty=1.3,
        has_critical_degrade=False,
        degraded_boards=[],
        isolated_outlier_boards=[],
        compared_board_count=3,
        board_scores=board_scores,
    )

    # Test _build_score_statistics
    stats = mixin._build_score_statistics(total_detail)

    print("=== Score Statistics Test ===")
    print(f"Min board score: {stats['min_board_score']}")
    print(f"Max board score: {stats['max_board_score']}")
    print(f"Avg board score: {stats['avg_board_score']}")
    print(f"Median board score: {stats['median_board_score']}")
    print(f"Best board ID: {stats['best_board_id']}")
    print(f"Worst board ID: {stats['worst_board_id']}")

    assert stats['min_board_score'] == 15.0, f"Expected 15.0, got {stats['min_board_score']}"
    assert stats['max_board_score'] == 25.0, f"Expected 25.0, got {stats['max_board_score']}"
    assert stats['best_board_id'] == 'mk_1', f"Expected mk_1, got {stats['best_board_id']}"
    assert stats['worst_board_id'] == 'mk_2', f"Expected mk_2, got {stats['worst_board_id']}"

    print("[PASS] Score statistics test passed\n")


if __name__ == '__main__':
    try:
        test_build_score_statistics()
        print("All tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"[FAIL] Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
