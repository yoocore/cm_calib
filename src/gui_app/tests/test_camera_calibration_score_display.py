from __future__ import annotations

from src.calibration.camera_calibration import BoardScoreDetail, TotalScoreDetail, _build_annotation_legend_lines


class TestCameraCalibrationScoreDisplay:
    def test_annotation_legend_marks_excluded_outlier_boards(self):
        total_detail = TotalScoreDetail(
            success=True,
            total_score=51.0,
            raw_total_score=1090.0,
            degrade_penalty=0.0,
            has_critical_degrade=False,
            degraded_boards=[],
            isolated_outlier_boards=["mk_8"],
            compared_board_count=2,
            board_scores=[
                BoardScoreDetail(
                    board_id="mk_8",
                    board_type="marker",
                    success=True,
                    compared=True,
                    reference_visible=True,
                    sim_visible=True,
                    total_score=1039.0,
                    rmse=1039.0,
                    mean_error=1039.0,
                    max_error=1039.0,
                    miss_rate=0.0,
                    matched_point_count=8,
                ),
                BoardScoreDetail(
                    board_id="mk_7",
                    board_type="marker",
                    success=True,
                    compared=True,
                    reference_visible=True,
                    sim_visible=True,
                    total_score=51.0,
                    rmse=51.0,
                    mean_error=51.0,
                    max_error=51.0,
                    miss_rate=0.0,
                    matched_point_count=8,
                ),
            ],
        )

        legend_lines = _build_annotation_legend_lines(total_detail)

        assert legend_lines == [
            "mk_8: 1039.000 (excluded)",
            "mk_7: 51.000",
        ]
