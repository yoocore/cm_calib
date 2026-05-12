from __future__ import annotations

from pathlib import Path

import pytest

from gui_app.services.static_vehicle_reader import (
    build_vehicle_path,
    parse_testrun_for_vehicle,
    read_vehicle_sensors,
    resolve_vehicle_info,
)


class TestEdgeCases:
    def test_parse_testrun_file_not_found(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError):
            parse_testrun_for_vehicle(missing)

    def test_parse_testrun_empty_file(self, tmp_path):
        empty = tmp_path / "empty"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="does not contain"):
            parse_testrun_for_vehicle(empty)

    def test_read_vehicle_file_not_found(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError):
            read_vehicle_sensors(missing)

    def test_resolve_vehicle_info_testrun_not_found(self, tmp_path):
        project_root = tmp_path / "project"
        (project_root / "Data" / "TestRun").mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            resolve_vehicle_info(project_root, "nonexistent_testrun")

    def test_resolve_vehicle_info_vehicle_not_found(self, tmp_path):
        project_root = tmp_path / "project"
        (project_root / "Data" / "TestRun").mkdir(parents=True)
        tst = project_root / "Data" / "TestRun" / "my_testrun"
        tst.write_text("Vehicle = Examples/MissingVehicle\n", encoding="utf-8")
        with pytest.raises(FileNotFoundError):
            resolve_vehicle_info(project_root, "my_testrun")

    def test_build_vehicle_path_outside_project(self, tmp_path):
        project_root = tmp_path / "project"
        path = build_vehicle_path(project_root, "../../../malicious")
        expected = (project_root / "Data" / "Vehicle" / ".." / ".." / ".." / "malicious").resolve()
        assert path == expected
        assert not path.exists()
