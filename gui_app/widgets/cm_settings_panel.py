from __future__ import annotations

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
    "padding: 12px;"
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
    "padding: 14px;"
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

        self.precheck_button = QPushButton("Check Inputs")
        self.generate_config_button = QPushButton("Generate Configs")
        self._generate_configs_ready = False
        self.generate_config_button.setEnabled(False)

        self.precheck_tree = QTreeWidget()
        self.precheck_tree.setColumnCount(4)
        self.precheck_tree.setHeaderLabels(["Camera", "Check", "Config", "Message"])
        self.precheck_tree.setRootIsDecorated(False)
        self.precheck_tree.header().setStretchLastSection(True)
        self.precheck_tree.header().setDefaultAlignment(Qt.AlignLeft)
        self.precheck_tree.setColumnWidth(1, 50)

        self.browse_button.clicked.connect(self._browse_project_root)
        self.testrun_browse_button.clicked.connect(self._browse_testrun)
        self.precheck_button.clicked.connect(self.precheck_clicked.emit)
        self.generate_config_button.clicked.connect(self.generate_config_clicked.emit)
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

        self.project_group = _SectionGroup("Project Inputs", self)
        project_layout = QVBoxLayout(self.project_group)
        project_layout.setContentsMargins(10, 6, 10, 10)
        project_layout.addLayout(form)

        self.camera_group = _SectionGroup("Camera Selection", self)
        camera_layout = QVBoxLayout(self.camera_group)
        camera_layout.setContentsMargins(10, 6, 10, 10)
        camera_layout.setSpacing(8)
        self.camera_list.setMinimumHeight(150)
        camera_layout.addWidget(self.camera_list, 1)
        camera_layout.addWidget(precheck_row)

        self.results_group = _SectionGroup("Check Results", self)
        results_layout = QVBoxLayout(self.results_group)
        results_layout.setContentsMargins(10, 6, 10, 10)
        results_layout.setSpacing(8)
        self.precheck_tree.setMinimumHeight(140)
        results_layout.addWidget(self.precheck_tree, 1)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(self.project_group)
        layout.addWidget(self.camera_group, 1)
        layout.addWidget(self.results_group, 1)

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
        for camera_name in cameras:
            item = QListWidgetItem(camera_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.camera_list.addItem(item)
        self.clear_precheck_results()

    def selected_cameras(self) -> list[str]:
        selected: list[str] = []
        for index in range(self.camera_list.count()):
            item = self.camera_list.item(index)
            if item.checkState() == Qt.Checked:
                selected.append(item.text())
        return selected

    def update_precheck_results(self, results: list[dict]) -> None:
        self.precheck_tree.clear()
        for result in results:
            camera_name = str(result.get("camera") or "")
            ok = bool(result.get("ok"))
            item = QTreeWidgetItem(self.precheck_tree)
            item.setText(0, camera_name)
            item.setData(0, Qt.TextAlignmentRole, Qt.AlignLeft | Qt.AlignVCenter)
            status_text = "✓" if ok else "✗"
            item.setText(1, status_text)
            item.setForeground(1, _GREEN if ok else _RED)
            msg = str(result.get("message") or "")
            item.setToolTip(0, camera_name)
            check_text = f"{status_text} {msg}" if not ok and msg else status_text
            item.setText(1, check_text)
            item.setToolTip(1, msg)
            config_parts: list[str] = []
            for key in ("config_path", "backup_path", "preview_path"):
                v = str(result.get(key) or "")
                if v:
                    config_parts.append(v)
            config_text = "; ".join(config_parts) if config_parts else ""
            item.setText(2, config_text)
            if config_text:
                item.setToolTip(2, config_text)
            detail_lines: list[str] = []
            for p in result.get("raw_matches", []):
                s = str(p).strip()
                if s:
                    detail_lines.append(s)
            for p in result.get("annotated_matches", []):
                s = str(p).strip()
                if s:
                    detail_lines.append(s)
            detail_text = "\n".join(detail_lines)
            item.setText(3, detail_text)
            if detail_text:
                item.setToolTip(3, detail_text)
        self._generate_configs_ready = bool(results) and all(
            bool(r.get("ok")) for r in results
        )
        self.generate_config_button.setEnabled(self._generate_configs_ready)

    def clear_precheck_results(self) -> None:
        self.precheck_tree.clear()
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
