from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui_app.widgets.calibration_panel import detect_cm_versions

_DEFAULT_BROWSE_ROOT = "C:/CM_Projects"
_GREEN = QBrush(QColor("#4caf50"))
_RED = QBrush(QColor("#e53935"))
_CHECKBOX_SVG = (Path(__file__).resolve().parent.parent / "checkbox_checked.svg").as_posix()
_DROPDOWN_ARROW_SVG = (Path(__file__).resolve().parent.parent / "dropdown_arrow.svg").as_posix()
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

_CONTROL_STYLE = (
    "QLineEdit {"
    "border: 1px solid #cbd5e1;"
    "border-radius: 6px;"
    "padding: 4px 10px;"
    "background-color: #ffffff;"
    "color: #1e293b;"
    "}"
    "QLineEdit:disabled {"
    "background-color: #f1f5f9;"
    "color: #94a3b8;"
    "border-color: #e2e8f0;"
    "}"
    "QComboBox {"
    "border: 1px solid #cbd5e1;"
    "border-radius: 6px;"
    "padding: 4px 10px;"
    "background-color: #ffffff;"
    "color: #1e293b;"
    "}"
    "QComboBox:disabled {"
    "background-color: #f1f5f9;"
    "color: #94a3b8;"
    "border-color: #e2e8f0;"
    "}"
    "QComboBox::drop-down {"
    "border: none;"
    "width: 28px;"
    "background: transparent;"
    "border-left: 1px solid #e2e8f0;"
    "border-top-right-radius: 6px;"
    "border-bottom-right-radius: 6px;"
    "}"
    "QComboBox::down-arrow {"
    f"image: url({_DROPDOWN_ARROW_SVG});"
    "width: 10px;"
    "height: 10px;"
    "}"
    "QPushButton {"
    "background-color: #ffffff;"
    "color: #1e293b;"
    "border: 1px solid #cbd5e1;"
    "border-radius: 6px;"
    "padding: 6px 10px;"
    "}"
    "QPushButton:hover {"
    "background-color: #f8fafc;"
    "border-color: #94a3b8;"
    "}"
    "QPushButton:disabled {"
    "color: #94a3b8;"
    "background-color: #f1f5f9;"
    "border-color: #e2e8f0;"
    "}"
    "QListWidget {"
    "border: 1px solid #cbd5e1;"
    "border-radius: 6px;"
    "background-color: #ffffff;"
    "}"
    "QListWidget::item:selected {"
    "background-color: #e8f0fe;"
    "}"
)


_CHECKBOX_STYLE = (
    "QListWidget::indicator {"
    "    width: 16px;"
    "    height: 16px;"
    "    border: 2px solid #6b7280;"
    "    border-radius: 3px;"
    "    background-color: #ffffff;"
    "}"
    "QListWidget::indicator:checked {"
    f"    image: url({_CHECKBOX_SVG});"
    "    border: 2px solid #6b7280;"
    "    border-radius: 3px;"
    "    background-color: #ffffff;"
    "}"
    "QListWidget::indicator:unchecked {"
    "    background-color: #ffffff;"
    "}"
)


class _SectionGroup(QGroupBox):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self.setStyleSheet(_SECTION_GROUP_STYLE)


class CmSettingsPanel(QGroupBox):
    project_root_changed = Signal(str)
    testrun_changed = Signal(str)
    wizard_for_camera_clicked = Signal(str)
    camera_selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("CM Settings", parent)
        self.setStyleSheet(_PANEL_STYLE + _CONTROL_STYLE + _CHECKBOX_STYLE)

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
        self.camera_list.setStyleSheet(_CHECKBOX_STYLE)

        self._camera_check_widgets: dict[str, tuple] = {}
        self._has_precheck_results: bool = False
        self._prechecked_camera_names: set[str] = set()

        self.browse_button.clicked.connect(self._browse_project_root)
        self.testrun_browse_button.clicked.connect(self._browse_testrun)
        self.project_root_edit.editingFinished.connect(
            lambda: self.project_root_changed.emit(self.project_root_edit.text())
        )
        self.testrun_edit.editingFinished.connect(
            lambda: self.testrun_changed.emit(self.testrun_edit.text())
        )
        self.camera_list.itemChanged.connect(self._on_camera_selection_changed)
        self.camera_list.itemClicked.connect(self._toggle_check_state)
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

        cm_versions = detect_cm_versions()
        self.maker_combo = QComboBox()
        self.maker_combo.addItem("Maker Type", None)  # placeholder for user confirmation
        self.maker_combo.addItem("CarMaker", "carmaker")
        self.maker_combo.addItem("TruckMaker", "truckmaker")
        self.maker_combo.addItem("MCycleMaker", "mcyclemaker")
        self.maker_combo.setFixedWidth(120)
        self.maker_combo.setFixedHeight(30)

        self.cm_version_combo = QComboBox()
        self.cm_version_combo.addItem("Select CM version", None)
        if not cm_versions:
            self.cm_version_combo.setItemText(0, "Select CM version")
        else:
            for ver, install_path in cm_versions.items():
                self.cm_version_combo.addItem(ver, install_path)
            self.cm_version_combo.setCurrentIndex(0)
        idx = self.cm_version_combo.count()
        self.cm_version_combo.addItem("not support ver.CM14-")
        self.cm_version_combo.model().item(idx).setEnabled(False)
        self.cm_version_combo.setFixedHeight(30)

        cm_row = QWidget()
        cm_row_layout = QHBoxLayout(cm_row)
        cm_row_layout.setContentsMargins(0, 0, 0, 0)
        cm_row_layout.addWidget(self.maker_combo)
        cm_row_layout.addWidget(self.cm_version_combo, 1)

        self.project_group = _SectionGroup("Project Inputs", self)
        project_layout = QVBoxLayout(self.project_group)
        project_layout.setContentsMargins(10, 6, 10, 8)
        project_layout.addLayout(form)
        project_layout.addWidget(cm_row)

        self.camera_group = _SectionGroup("Camera Selection", self)
        camera_layout = QVBoxLayout(self.camera_group)
        camera_layout.setContentsMargins(8, 4, 8, 6)
        camera_layout.setSpacing(6)
        self.camera_list.setMinimumHeight(200)
        camera_layout.addWidget(self.camera_list, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(6)
        layout.addWidget(self.project_group)
        layout.addWidget(self.camera_group, 1)

    @property
    def cm_install_path(self) -> Path | None:
        data = self.cm_version_combo.currentData()
        if isinstance(data, Path):
            return data
        return None

    @property
    def maker_type(self) -> str | None:
        data = self.maker_combo.currentData()
        if isinstance(data, str):
            return data
        return None

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
        # Restore mapping status for all cameras after clearing precheck
        for _cn in self._camera_check_widgets:
            self._update_row_status(str(_cn))
        self.camera_selection_changed.emit()

    def _on_camera_rows_moved(self, *_args) -> None:
        self.clear_precheck_results()
        self.camera_selection_changed.emit()

    def _toggle_check_state(self, item: QListWidgetItem) -> None:
        """setItemWidget hides native checkbox — toggle on row click instead."""
        current = item.checkState()
        # blockSignals prevents _on_camera_selection_changed → clear_precheck_results()
        # from wiping ALL check_labels before _update_row_status restores the single one
        self.camera_list.blockSignals(True)
        item.setCheckState(Qt.Unchecked if current == Qt.Checked else Qt.Checked)
        self.camera_list.blockSignals(False)
        camera_name = item.data(Qt.UserRole)
        is_checked = item.checkState() == Qt.Checked
        if camera_name:
            cn = str(camera_name)
            self._update_row_status(cn)
            if not is_checked:
                pair = self._camera_check_widgets.get(cn)
                if pair:
                    pair[0].setText("")  # clear check_label for unchecked camera
        self.camera_selection_changed.emit()

    def selected_cameras(self) -> list[str]:
        selected: list[str] = []
        for index in range(self.camera_list.count()):
            item = self.camera_list.item(index)
            if item.checkState() == Qt.Checked:
                data = item.data(Qt.UserRole)
                selected.append(str(data) if data is not None else item.text())
        return selected

    def set_cameras(self, cameras: list[str]) -> None:
        # Preserve check states before clearing
        checked_names: set[str] = set()
        for index in range(self.camera_list.count()):
            item = self.camera_list.item(index)
            if item and item.checkState() == Qt.Checked:
                data = item.data(Qt.UserRole)
                if data is not None:
                    checked_names.add(str(data))
        self.camera_list.clear()
        self._camera_check_widgets.clear()
        self._has_precheck_results = False
        mapping = self._load_camera_mapping()
        for camera_name in cameras:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, camera_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if camera_name in checked_names else Qt.Unchecked)
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

            # Visual separator
            sep = QWidget()
            sep.setFixedWidth(4)
            row_layout.addWidget(sep)

            wizard_btn = QPushButton("Wizard")
            wizard_btn.setFixedWidth(70)
            wizard_btn.clicked.connect(
                lambda checked, cn=camera_name: self.wizard_for_camera_clicked.emit(cn)
            )
            row_layout.addWidget(wizard_btn)

            open_btn = QPushButton("Config")
            open_btn.setFixedWidth(70)
            open_btn.setEnabled(False)
            open_btn.setToolTip("No config generated yet")
            open_btn.clicked.connect(
                lambda checked, cn=camera_name: self._on_config_clicked(cn)
            )
            row_layout.addWidget(open_btn)

            item.setSizeHint(row_widget.sizeHint())
            self.camera_list.setItemWidget(item, row_widget)
            self._camera_check_widgets[camera_name] = (check_label, open_btn)
            self._update_row_status(camera_name)

    def _load_camera_mapping(self) -> dict:
        project_dir = self.project_root_edit.text().strip()
        if not project_dir:
            return {}
        from src.gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
        mapping_path = mapping_path_for_project(project_dir)
        if mapping_path.exists():
            config = json.loads(mapping_path.read_text(encoding="utf-8"))
        else:
            config = {}
        result: dict = {}
        for name, entry in config.items():
            folder = entry.get("config_folder", "")
            if folder:
                result[name] = folder
        return result

    def _on_config_clicked(self, camera_name: str) -> None:
        project_dir = self.project_root_edit.text().strip()
        if not project_dir:
            return
        from src.gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
        mapping_path = mapping_path_for_project(project_dir)
        config_path = ""
        if mapping_path.exists():
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            entry = mapping.get(camera_name, {})
            config_path = entry.get("config_folder", "")
        if not config_path:
            QMessageBox.information(self, "No Config", "No config yet. Use Wizard first.")
            return
        if os.path.isdir(config_path):
            os.startfile(config_path)
            return
        self._show_relocate_dialog(camera_name, config_path)

    def _show_relocate_dialog(self, camera_name: str, old_path: str) -> None:
        from src.gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
        msg = QMessageBox(QMessageBox.Warning, "Config Not Found",
            f"Config folder not found:\n{old_path}\n\nLocate the new location?",
            QMessageBox.Yes | QMessageBox.No, self)
        if msg.exec() != QMessageBox.Yes:
            return
        new_path = QFileDialog.getExistingDirectory(
            self, "Locate Config Folder", str(Path(old_path).parent))
        if not new_path:
            return
        project_dir = self.project_root_edit.text().strip()
        if not project_dir:
            return
        mapping_path = mapping_path_for_project(project_dir)
        if mapping_path.exists():
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        else:
            mapping = {}
        if camera_name not in mapping:
            mapping[camera_name] = {}
        mapping[camera_name]["config_folder"] = new_path
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=4), encoding="utf-8")
        self._update_row_status(camera_name)

    def _update_row_status(self, camera_name: str) -> None:
        widgets = self._camera_check_widgets.get(camera_name)
        if not widgets:
            return
        check_label, open_btn = widgets
        project_dir = self.project_root_edit.text().strip()
        if not project_dir:
            check_label.setText("")
            open_btn.setEnabled(False)
            open_btn.setToolTip("")
            return
        from src.gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
        mapping_path = mapping_path_for_project(project_dir)
        config_path = ""
        if mapping_path.exists():
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            entry = mapping.get(camera_name, {})
            config_path = entry.get("config_folder", "")
        if not config_path:
            check_label.setText("")
            open_btn.setEnabled(False)
            open_btn.setToolTip("No config generated yet")
        elif os.path.isdir(config_path):
            check_label.setText("●")
            check_label.setStyleSheet("color: #4caf50; font-weight: bold;")
            open_btn.setEnabled(True)
            open_btn.setToolTip(config_path)
        else:
            check_label.setText("✗")
            check_label.setStyleSheet("color: #e53935; font-weight: bold;")
            open_btn.setEnabled(False)
            open_btn.setToolTip(f"Config folder not found:\n{config_path}")

    def update_precheck_results(self, results: list[dict]) -> None:
        self._has_precheck_results = True
        for result in results:
            camera_name = str(result.get("camera") or "")
            self._prechecked_camera_names.add(camera_name)
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

    def clear_precheck_results(self) -> None:
        self._has_precheck_results = False
        self._prechecked_camera_names.clear()
        for camera_name, (check_label, _) in self._camera_check_widgets.items():
            check_label.setToolTip("")

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
        self.maker_combo.setEnabled(not locked)
        self.cm_version_combo.setEnabled(not locked)
