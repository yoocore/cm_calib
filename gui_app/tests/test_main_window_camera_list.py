from __future__ import annotations

from gui_app.main_window import MainWindow


def _create_project(project_root):
    (project_root / "Data" / "Script" / "CameraCalibration" / "configs").mkdir(parents=True)
    (project_root / "Data" / "TestRun").mkdir(parents=True)
    (project_root / "Data" / "Vehicle" / "Examples").mkdir(parents=True)
    (project_root / "Movie").mkdir()

    (project_root / "Data" / "TestRun" / "vctc_ngxpro").write_text(
        "#INFOFILE1.1 (UTF-8) - Do not remove this line!\n"
        "FileIdent = CarMaker-TestRun 14\n"
        "Vehicle = Examples/TestVehicle\n",
        encoding="utf-8",
    )
    (project_root / "Data" / "Vehicle" / "Examples" / "TestVehicle").write_text(
        "Sensor.0.name = cam1\n"
        "Sensor.0.Active = 1\n"
        "Sensor.Param.0.Type = CameraRSI\n"
        "Sensor.Param.0.Name = cam1\n",
        encoding="utf-8",
    )
    (project_root / "Data" / "Script" / "CameraCalibration" / "configs" / "camera.cam1.json").write_text(
        '{"camera": "cam1"}',
        encoding="utf-8",
    )


class TestMainWindowCameraList:
    def test_main_window_uses_two_column_layout(self, qtbot, tmp_path):
        project_root = tmp_path / "project"
        _create_project(project_root)

        window = MainWindow(project_root)
        qtbot.addWidget(window)

        outer_splitter = window.centralWidget()
        assert outer_splitter.count() == 2
        left_mid_container = outer_splitter.widget(0)
        inner_splitter = left_mid_container.findChild(type(outer_splitter))
        assert inner_splitter.count() == 2
        assert window.cm_settings_panel.parentWidget() is inner_splitter
        assert window.calibration_panel.parentWidget() is inner_splitter
        assert not hasattr(window.cm_settings_panel, "sensor_list")

    def test_camera_list_stays_empty_without_testrun(self, qtbot, tmp_path):
        project_root = tmp_path / "project"
        _create_project(project_root)

        window = MainWindow(project_root)
        qtbot.addWidget(window)

        assert window.cm_settings_panel.testrun_edit.text() == ""
        assert window.cm_settings_panel.camera_list.count() == 0

    def test_camera_list_populates_after_testrun_selected(self, qtbot, tmp_path):
        project_root = tmp_path / "project"
        _create_project(project_root)

        window = MainWindow(project_root)
        qtbot.addWidget(window)

        window.cm_settings_panel.testrun_edit.setText("vctc_ngxpro")
        window._on_testrun_changed("vctc_ngxpro")

        assert window.cm_settings_panel.camera_list.count() == 1
        assert window.cm_settings_panel.camera_list.item(0).text() == "cam1"
