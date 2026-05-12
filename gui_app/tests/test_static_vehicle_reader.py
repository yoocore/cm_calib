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
    def test_filters_to_camera_rsi_only(self, tmp_path):
        vfile = tmp_path / "vehicle"
        vfile.write_text(
            "Sensor.0.name = FW\n"
            "Sensor.0.Active = 0\n"
            "Sensor.1.name = right_rear\n"
            "Sensor.1.Active = 1\n"
            "Sensor.2.name = Rear\n"
            "Sensor.2.Active = 0\n"
            "Sensor.3.name = TF\n"
            "Sensor.3.Active = 0\n"
            "Sensor.4.name = rear_tv\n"
            "Sensor.4.Active = 1\n"
            "Sensor.5.name = left_tv\n"
            "Sensor.5.Active = 0\n"
            "Sensor.6.name = TRight\n"
            "Sensor.6.Active = 0\n"
            "Sensor.Param.0.Type = SAngle\n"
            "Sensor.Param.0.Name = SL_Param\n"
            "Sensor.Param.1.Type = CameraRSI\n"
            "Sensor.Param.1.Name = FrontWide\n"
            "Sensor.Param.2.Type = CameraRSI\n"
            "Sensor.Param.2.Name = Rear\n"
            "Sensor.Param.3.Type = CameraRSI\n"
            "Sensor.Param.3.Name = TopView_Front\n"
            "Sensor.Param.4.Type = CameraRSI\n"
            "Sensor.Param.4.Name = TopView_Rear\n"
            "Sensor.Param.5.Type = CameraRSI\n"
            "Sensor.Param.5.Name = TopView_Left\n"
            "Sensor.Param.6.Type = CameraRSI\n"
            "Sensor.Param.6.Name = TopView_Right\n",
            encoding="utf-8",
        )
        sensors = read_vehicle_sensors(vfile)
        names = [s["name"] for s in sensors]
        assert "FW" in names          # FrontWide → FW (substring match)
        assert "Rear" in names        # exact match
        assert "TF" in names          # TopView_Front → TF (substring)
        assert "rear_tv" in names     # TopView_Rear → rear_tv (substring)
        assert "left_tv" in names     # TopView_Left → left_tv (substring)
        assert "right_rear" in names  # TopView_Right → right_rear
        assert "TRight" in names      # TopView_Right → TRight (substring)
        # Sensor 0 is CameraRSI via Param.1.Name = FrontWide → FW
        assert sensors[0]["name"] == "FW"
        assert sensors[0]["active"] is False
        # Sensor 4 (rear_tv) is active
        rear_tv = [s for s in sensors if s["name"] == "rear_tv"][0]
        assert rear_tv["active"] is True

    def test_excludes_non_camera_sensors(self, tmp_path):
        vfile = tmp_path / "vehicle"
        vfile.write_text(
            "Sensor.0.name = SteeringAngle\n"
            "Sensor.0.Active = 1\n"
            "Sensor.Param.0.Type = SAngle\n"
            "Sensor.Param.0.Name = SL_Param\n"
            "Sensor.Param.1.Type = CameraRSI\n"
            "Sensor.Param.1.Name = FrontCam\n",
            encoding="utf-8",
        )
        sensors = read_vehicle_sensors(vfile)
        # SteeringAngle should be excluded (type = SAngle, not CameraRSI)
        names = [s["name"] for s in sensors]
        assert "SteeringAngle" not in names

    def test_empty_file_returns_empty_list(self, tmp_path):
        vfile = tmp_path / "empty"
        vfile.write_text("", encoding="utf-8")
        assert read_vehicle_sensors(vfile) == []

    def test_no_camera_rsi_params_returns_empty(self, tmp_path):
        vfile = tmp_path / "vehicle"
        vfile.write_text(
            "Sensor.0.name = FW\n"
            "Sensor.0.Active = 0\n"
            "Sensor.1.name = Rear\n"
            "Sensor.Param.0.Type = SAngle\n"
            "Sensor.Param.0.Name = SL_Param\n",
            encoding="utf-8",
        )
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
            "Sensor.1.Active = 0\n"
            "Sensor.Param.0.Type = CameraRSI\n"
            "Sensor.Param.0.Name = cam1\n"
            "Sensor.Param.1.Type = CameraRSI\n"
            "Sensor.Param.1.Name = cam2\n",
            encoding="utf-8",
        )

        info = resolve_vehicle_info(project_root, "my_testrun")
        assert info["vehicle_key"] == "Examples/MyVehicle"
        assert len(info["sensors"]) == 2
        assert info["sensors"][0]["name"] == "cam1"
        assert info["sensors"][0]["active"] is True
        assert info["sensors"][1]["name"] == "cam2"
        assert info["sensors"][1]["active"] is False
