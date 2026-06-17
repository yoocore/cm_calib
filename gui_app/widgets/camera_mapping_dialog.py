from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QMessageBox,
    QAbstractItemView,
)

from gui_app.services.static_vehicle_reader import resolve_vehicle_info

MAPPING_FILENAME = "calibtool_camera_config.json"

_IMAGE_SUFFIXES = "Images (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)"


def mapping_path_for_project(project_root: str) -> Path:
    return Path(project_root) / "Movie" / MAPPING_FILENAME


def load_camera_config(project_root: str) -> dict:
    path = mapping_path_for_project(project_root)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_camera_config(project_root: str, mapping: dict) -> Path:
    path = mapping_path_for_project(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=4)
    return path


class CameraMappingDialog(QDialog):

    def __init__(
        self,
        project_root: str,
        testrun: str,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Camera Mapping")
        self.resize(800, 500)
        self._project_root = project_root
        self._testrun = testrun
        self._mapping: dict = load_camera_config(project_root)

        layout = QVBoxLayout(self)

        header = QLabel(
            "Map each vehicle sensor to its real camera image.\n"
            "Config folders are auto-filled from existing wizard output."
        )
        layout.addWidget(header)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Sensor Name", "Real Image", "Config Folder"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._on_save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._populate()

    def _populate(self) -> None:
        try:
            info = resolve_vehicle_info(Path(self._project_root), self._testrun)
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Cannot read vehicle info: {exc}")
            return

        sensors = info.get("sensors", [])
        self._table.setRowCount(len(sensors))

        for row, sensor in enumerate(sensors):
            name = sensor["name"]
            entry = self._mapping.get(name, {})
            real_image = entry.get("real_image", "")
            config_folder = entry.get("config_folder", "")

            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, sensor.get("index", 0))
            self._table.setItem(row, 0, name_item)

            img_widget = self._make_browse_cell(real_image)
            self._table.setCellWidget(row, 1, img_widget)

            cfg_item = QTableWidgetItem(config_folder)
            cfg_item.setFlags(cfg_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 2, cfg_item)

    def _make_browse_cell(self, initial_path: str) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 2, 2, 2)
        h.setSpacing(4)
        edit = QLineEdit(initial_path)
        edit.setObjectName("image_edit")
        btn = QPushButton("...")
        btn.setFixedWidth(30)
        btn.clicked.connect(lambda: self._browse_image(edit))
        h.addWidget(edit, 1)
        h.addWidget(btn)
        return w

    def _browse_image(self, edit: QLineEdit) -> None:
        start_dir = str(Path(self._project_root) / "Movie")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Real Image", start_dir, _IMAGE_SUFFIXES,
        )
        if path:
            edit.setText(path)

    def _on_save(self) -> None:
        mapping: dict = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if not name_item:
                continue
            name = name_item.text()
            sensor_index = name_item.data(Qt.UserRole) or 0

            img_widget = self._table.cellWidget(row, 1)
            edit = img_widget.findChild(QLineEdit, "image_edit") if img_widget else None
            real_image = edit.text().strip() if edit else ""

            cfg_item = self._table.item(row, 2)
            config_folder = cfg_item.text().strip() if cfg_item else ""

            if not real_image and not config_folder:
                continue

            mapping[name] = {
                "sensor_index": int(sensor_index),
                "real_image": real_image,
                "config_folder": config_folder,
            }

        saved_path = save_camera_config(self._project_root, mapping)
        QMessageBox.information(
            self, "Saved",
            f"Camera config saved:\n{saved_path}\n\n{len(mapping)} sensor(s) mapped.",
        )
        self.accept()
