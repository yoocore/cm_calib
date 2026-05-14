from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_DEFAULT_BROWSE_ROOT = "C:/CM_Projects"


class RuntimePanel(QGroupBox):
    project_root_changed = Signal(str)
    testrun_changed = Signal(str)
    status_query_clicked = Signal()

    def __init__(self, _project_root: Path, parent: QWidget | None = None):
        super().__init__("Runtime", parent)
        self.project_root_edit = QLineEdit()
        self.project_root_edit.setPlaceholderText("e.g. C:/CM_Projects/CMO141_Calibration")
        self.testrun_edit = QLineEdit()
        self.testrun_edit.setPlaceholderText("e.g. vctc_ngxpro")
        self.vehicle_label = QLabel("-")
        self.browse_button = QPushButton("Browse")
        self.testrun_browse_button = QPushButton("Browse")
        self.status_query_button = QPushButton("Query Status")

        self.browse_button.clicked.connect(self._browse_project_root)
        self.testrun_browse_button.clicked.connect(self._browse_testrun)
        self.status_query_button.clicked.connect(self.status_query_clicked.emit)
        self.project_root_edit.editingFinished.connect(lambda: self.project_root_changed.emit(self.project_root_edit.text()))
        self.testrun_edit.editingFinished.connect(lambda: self.testrun_changed.emit(self.testrun_edit.text()))

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

        wrapper = QVBoxLayout(self)
        wrapper.addLayout(form)
        wrapper.addWidget(self.status_query_button)
        wrapper.addStretch(1)

    def _browse_project_root(self) -> None:
        start = self.project_root_edit.text().strip() or _DEFAULT_BROWSE_ROOT
        path = QFileDialog.getExistingDirectory(self, "Select Project Root", start)
        if path:
            self.project_root_edit.setText(path)
            self.project_root_changed.emit(path)

    def _browse_testrun(self) -> None:
        start = Path(self.project_root_edit.text().strip() or _DEFAULT_BROWSE_ROOT) / "Data" / "TestRun"
        if not start.exists():
            start = Path(_DEFAULT_BROWSE_ROOT)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select TestRun", str(start), "TestRun files (*)")
        if path:
            self.testrun_edit.setText(path)
            self.testrun_changed.emit(path)

    def update_sensor_list(self, sensors: list[dict]) -> None:
        _ = sensors

    def set_runtime_summary(self, payload: dict) -> None:
        pass  # runtime summary displayed in calibration panel

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
        self.status_query_button.setEnabled(not locked)
        self.status_query_button.setToolTip(tip)
