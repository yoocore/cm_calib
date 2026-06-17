from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_DEFAULT_BROWSE_ROOT = "C:/CM_Projects"
_GREEN = QBrush(QColor("#4caf50"))
_RED = QBrush(QColor("#e53935"))
_SECTION_GROUP_STYLE = (
    "QGroupBox {"
    "border: 1px solid #d0d7de;"
    "border-radius: 10px;"
    "margin-top: 10px;"
    "padding: 4px;"
    "background-color: #ffffff;"
    "font-weight: 600;"
    "}"
    "QGroupBox::title {"
    "subcontrol-origin: margin;"
    "left: 10px;"
    "padding: 0 4px;"
    "color: #334155;"
    "}"
)

_PANEL_STYLE = (
    "QGroupBox {"
    "border: 1px solid #cbd5e1;"
    "border-radius: 12px;"
    "margin-top: 12px;"
    "padding: 10px;"
    "background-color: #ffffff;"
    "font-weight: 700;"
    "}"
    "QGroupBox::title {"
    "subcontrol-origin: margin;"
    "left: 12px;"
    "padding: 0 6px;"
    "color: #0f172a;"
    "}"
)


class _SectionGroup(QGroupBox):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self.setStyleSheet(_SECTION_GROUP_STYLE)


class CmSettingsPanel(QGroupBox):
    project_root_changed = Signal(str)
    testrun_changed = Signal(str)
    precheck_clicked = Signal()
    generate_config_clicked = Signal()
    wizard_clicked = Signal()
    camera_mapping_clicked = Signal()
    camera_selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("CM Settings", parent)
        self.setStyleSheet(_PANEL_STYLE)

        self.project_root_edit = QLineEdit()
        self.project_root_edit.setPlaceholderText("e.g. C:/CM_Projects/CMO141_Calibration")
        self.testrun_edit = QLineEdit()
        self.testrun_edit.setPlaceholderText("e.g. vctc_ngxpro")
        self.vehicle_label = QLabel("-")
        self.browse_button = QPushButton("Browse")
        self.testrun_browse_button = QPushButton("Browse")

        self.camera_list = QListWidget()
        self.camera_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.camera_list.setDefaultDropAction(Qt.MoveAction)
        self.camera_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.camera_list.setDragEnabled(True)
        self.camera_list.setAcceptDrops(True)
        self.camera_list.setDropIndicatorShown(True)
        self.camera_list.setToolTip("拖拽调整相机顺序")

        self.precheck_button = QPushButton("Check Inputs")
        self.generate_config_button = QPushButton("Generate Configs")
        self.wizard_button = QPushButton("Board Wizard")
        self._generate_configs_ready = False
        self.generate_config_button.setEnabled(False)

        self._camera_check_widgets: dict[str, tuple] = {}

        self.browse_button.clicked.connect(self._browse_project_root)
        self.testrun_browse_button.clicked.connect(self._browse_testrun)
        self.precheck_button.clicked.connect(self.precheck_clicked.emit)
        self.generate_config_button.clicked.connect(self.generate_config_clicked.emit)
        self.wizard_button.clicked.connect(self.wizard_clicked.emit)
        self.project_root_edit.editingFinished.connect(
            lambda: self.project_root_changed.emit(self.project_root_edit.text())
        )
        self.testrun_edit.editingFinished.connect(
            lambda: self.testrun_changed.emit(self.testrun_edit.text())
        )
        self.camera_list.itemChanged.connect(self._on_camera_selection_changed)
        self.camera_list.model().rowsMoved.connect(self._on_camera_rows_moved)

        proj_row = QWidget(self)
        proj_layout = QHBoxLayout(proj_row)
        proj_layout.setContentsMargins(0, 0, 0, 0)
        proj_layout.addWidget(self.project_root_edit, 1)
        proj_layout.addWidget(self.browse_button)

        testrun_row = QWidget(self)
        testrun_layout = QHBoxLayout(testrun_row)
        testrun_layout.setContentsMargins(0, 0, 0, 0)
        testrun_layout.addWidget(self.testrun_edit, 1)
        testrun_layout.addWidget(self.testrun_browse_button)

        form = QFormLayout()
        form.addRow("ProjectDir", proj_row)
        form.addRow("TestRun", testrun_row)
        form.addRow("Vehicle", self.vehicle_label)

        precheck_row = QWidget(self)
        precheck_layout = QHBoxLayout(precheck_row)
        precheck_layout.setContentsMargins(0, 0, 0, 0)
        precheck_layout.addWidget(self.precheck_button)
        precheck_layout.addWidget(self.generate_config_button)
        precheck_layout.addWidget(self.wizard_button)

        self.project_group = _SectionGroup("Project Inputs", self)
        project_layout = QVBoxLayout(self.project_group)
        project_layout.setContentsMargins(10, 6, 10, 8)
        project_layout.addLayout(form)

        self.camera_mapping_button = QPushButton("Camera Mapping")
        self.camera_mapping_button.setToolTip(
            "Map vehicle sensors to real camera images.\n"
            "Creates/edits calibtool_camera_config.json in the Movie folder."
        )
        self.camera_mapping_button.clicked.connect(self.camera_mapping_clicked.emit)

        self.camera_group = _SectionGroup("Camera Selection", self)
        camera_layout = QVBoxLayout(self.camera_group)
        camera_layout.setContentsMargins(8, 4, 8, 6)
        camera_layout.setSpacing(6)
        camera_layout.addWidget(self.camera_mapping_button)
        self.camera_list.setMinimumHeight(200)
        camera_layout.addWidget(self.camera_list, 1)
        camera_layout.addWidget(precheck_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(6)
        layout.addWidget(self.project_group)
        layout.addWidget(self.camera_group, 1)

    def _browse_project_root(self) -> None:
        start = self.project_root_edit.text().strip() or _DEFAULT_BROWSE_ROOT
        path = QFileDialog.getExistingDirectory(self, "Select Project Root", start)
        if path:
            self.project_root_edit.setText(path)
            self.project_root_changed.emit(path)

    def _browse_testrun(self) -> None:
        start = (
            Path(self.project_root_edit.text().strip() or _DEFAULT_BROWSE_ROOT)
            / "Data"
            / "TestRun"
        )
        if not start.exists():
            start = Path(_DEFAULT_BROWSE_ROOT)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select TestRun", str(start), "TestRun files (*)"
        )
        if path:
            self.testrun_edit.setText(path)
            self.testrun_changed.emit(path)

    def _on_camera_selection_changed(self, _item: QListWidgetItem) -> None:
        self.clear_precheck_results()
        self.camera_selection_changed.emit()

    def _on_camera_rows_moved(self, *_args) -> None:
        self.clear_precheck_results()
        self.camera_selection_changed.emit()

    def set_cameras(self, cameras: list[str]) -> None:
        self.camera_list.clear()
        self._camera_check_widgets.clear()
        mapping = self._load_camera_mapping()
        for camera_name in cameras:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, camera_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.camera_list.addItem(item)

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 2, 4, 2)
            row_layout.setSpacing(8)

            name_label = QLabel(f"⋮⋮ {camera_name}")
            row_layout.addWidget(name_label)
            row_layout.addStretch()

            check_label = QLabel("")
            check_label.setFixedWidth(20)
            row_layout.addWidget(check_label)

            config_path = mapping.get(camera_name, "")
            open_btn = QPushButton("Config")
            open_btn.setFixedWidth(60)
            open_btn.setEnabled(bool(config_path))
            if not config_path:
                open_btn.setToolTip("No config generated yet")
            else:
                open_btn.setToolTip(config_path)
                open_btn.clicked.connect(
                    lambda checked, p=config_path: self._open_config_folder(p)
                )
            row_layout.addWidget(open_btn)

            item.setSizeHint(row_widget.sizeHint())
            self.camera_list.setItemWidget(item, row_widget)
            self._camera_check_widgets[camera_name] = (check_label, open_btn)

    def _load_camera_mapping(self) -> dict:
        project_dir = self.project_root_edit.text().strip()
        if not project_dir:
            return {}
        from gui_app.widgets.camera_mapping_dialog import load_camera_config
        config = load_camera_config(project_dir)
        result: dict = {}
        for name, entry in config.items():
            folder = entry.get("config_folder", "")
            if folder:
                result[name] = folder
        return result

    def _open_config_folder(self, path: str) -> None:
        import os
        if os.path.isdir(path):
            os.startfile(path)

    def selected_cameras(self) -> list[str]:
        selected: list[str] = []
        for index in range(self.camera_list.count()):
            item = self.camera_list.item(index)
            if item.checkState() == Qt.Checked:
                data = item.data(Qt.UserRole)
                selected.append(str(data) if data is not None else item.text())
        return selected

    def update_precheck_results(self, results: list[dict]) -> None:
        for result in results:
            camera_name = str(result.get("camera") or "")
            ok = bool(result.get("ok"))
            msg = str(result.get("message") or "")
            widgets = self._camera_check_widgets.get(camera_name)
            if widgets:
                check_label, _ = widgets
                status_text = "✓" if ok else "✗"
                check_label.setText(status_text)
                check_label.setStyleSheet(
                    "color: #4caf50; font-weight: bold;" if ok else "color: #e53935; font-weight: bold;"
                )
                if msg:
                    check_label.setToolTip(msg)
        self._generate_configs_ready = bool(results) and all(
            bool(r.get("ok")) for r in results
        )
        self.generate_config_button.setEnabled(self._generate_configs_ready)

    def clear_precheck_results(self) -> None:
        for camera_name, (check_label, _) in self._camera_check_widgets.items():
            check_label.setText("")
            check_label.setToolTip("")
        self._generate_configs_ready = False
        self.generate_config_button.setEnabled(False)

    def update_sensor_list(self, sensors: list[dict]) -> None:
        _ = sensors

    def clear_sensor_list(self) -> None:
        return

    def set_inputs_locked(self, locked: bool) -> None:
        tip = "Stop calibration first to modify" if locked else ""
        self.project_root_edit.setEnabled(not locked)
        self.project_root_edit.setToolTip(tip)
        self.testrun_edit.setEnabled(not locked)
        self.testrun_edit.setToolTip(tip)
        self.browse_button.setEnabled(not locked)
        self.browse_button.setToolTip(tip)
        self.testrun_browse_button.setEnabled(not locked)
        self.testrun_browse_button.setToolTip(tip)
        self.camera_list.setEnabled(not locked)
        self.precheck_button.setEnabled(not locked)
        self.generate_config_button.setEnabled(
            (not locked) and self._generate_configs_ready
        )
