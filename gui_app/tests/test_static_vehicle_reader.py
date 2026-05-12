from __future__ import annotations

from pathlib import Path

import pytest

from gui_app.services.static_vehicle_reader import (
    build_vehicle_path,
    parse_testrun_for_vehicle,
    read_vehicle_sensors,
    resolve_vehicle_info,
)


class TestParseTestrunForVehicle:
    def test_extracts_vehicle_key(self, tmp_path):
        tst = tmp_path / "testrun.tst"
        tst.write_text(
            "#INFOFILE1.1 (UTF-8) - Do not remove this line!\n"
            "FileIdent = CarMaker-TestRun 14\n"
            "Vehicle = Examples/Demo_Toyota_Camry_pro\n"
            "Road.FName = track.rd5\n",
            encoding="utf-8",
        )
        assert parse_testrun_for_vehicle(tst) == "Examples/Demo_Toyota_Camry_pro"

    def test_raises_when_vehicle_missing(self, tmp_path):
        tst = tmp_path / "testrun.tst"
        tst.write_text("FileIdent = CarMaker-TestRun 14\n", encoding="utf-8")
        with pytest.raises(ValueError, match="does not contain"):
            parse_testrun_for_vehicle(tst)

    def test_handles_windows_backslash(self, tmp_path):
        tst = tmp_path / "testrun.tst"
        tst.write_text("Vehicle = Examples\\Demo_Car\n", encoding="utf-8")
        assert parse_testrun_for_vehicle(tst) == "Examples\\Demo_Car"


class TestBuildVehiclePath:
    def test_builds_vehicle_path(self, tmp_path):
        project_root = tmp_path / "project"
        path = build_vehicle_path(project_root, "Examples/MyCar")
        expected = (project_root / "Data" / "Vehicle" / "Examples" / "MyCar").resolve()
        assert path == expected

    def test_handles_backslash(self, tmp_path):
        project_root = tmp_path / "project"
        path = build_vehicle_path(project_root, "Examples\\MyCar")
        expected = (project_root / "Data" / "Vehicle" / "Examples" / "MyCar").resolve()
        assert path == expected


class TestReadVehicleSensors:
    def test_reads_sensors_with_active_status(self, tmp_path):
        vfile = tmp_path / "vehicle"
        vfile.write_text(
            "Sensor.0.name = FW\n"
            "Sensor.0.Active = 1\n"
            "Sensor.1.name = Rear\n"
            "Sensor.1.Active = 0\n"
            "Sensor.2.name = left_tv\n"
            "Sensor.2.Active = 1\n",
            encoding="utf-8",
        )
        sensors = read_vehicle_sensors(vfile)
        assert len(sensors) == 3
        assert sensors[0] == {"index": 0, "name": "FW", "active": True}
        assert sensors[1] == {"index": 1, "name": "Rear", "active": False}
        assert sensors[2] == {"index": 2, "name": "left_tv", "active": True}

    def test_empty_file_returns_empty_list(self, tmp_path):
        vfile = tmp_path / "empty"
        vfile.write_text("", encoding="utf-8")
        assert read_vehicle_sensors(vfile) == []

    def test_no_sensor_entries_returns_empty(self, tmp_path):
        vfile = tmp_path / "vehicle"
        vfile.write_text("SomeKey = 123\nOther = value\n", encoding="utf-8")
        assert read_vehicle_sensors(vfile) == []


class TestResolveVehicleInfo:
    def test_resolves_full_info(self, tmp_path):
        project_root = tmp_path / "project"
        (project_root / "Data" / "TestRun").mkdir(parents=True)
        (project_root / "Data" / "Vehicle" / "Examples").mkdir(parents=True)

        tst = project_root / "Data" / "TestRun" / "my_testrun"
        tst.write_text("Vehicle = Examples/MyVehicle\n", encoding="utf-8")

        vfile = project_root / "Data" / "Vehicle" / "Examples" / "MyVehicle"
        vfile.write_text(
            "Sensor.0.name = cam1\n"
            "Sensor.0.Active = 1\n"
            "Sensor.1.name = cam2\n"
            "Sensor.1.Active = 0\n",
            encoding="utf-8",
        )

        info = resolve_vehicle_info(project_root, "my_testrun")
        assert info["vehicle_key"] == "Examples/MyVehicle"
        assert len(info["sensors"]) == 2
        assert info["sensors"][0]["name"] == "cam1"
        assert info["sensors"][0]["active"] is True
        assert info["sensors"][1]["name"] == "cam2"
        assert info["sensors"][1]["active"] is False
