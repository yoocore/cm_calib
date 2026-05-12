from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_CM_ROOTS = ["D:/IPG/carmaker", "C:/IPG/carmaker"]


def detect_cm_versions() -> dict[str, Path]:
    versions: dict[str, Path] = {}
    for root in _CM_ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for entry in root_path.iterdir():
            if not entry.is_dir() or not entry.name.startswith("win64-"):
                continue
            cm_office = entry / "GUI" / "CM_Office.exe"
            if cm_office.is_file():
                version = entry.name[len("win64-"):]
                versions[version] = entry
    return dict(sorted(versions.items(), key=lambda x: x[0], reverse=True))


_GREEN = QBrush(QColor("#4caf50"))
_GRAY = QBrush(QColor("#888888"))


class RuntimePanel(QGroupBox):
    def __init__(self, project_root: Path, parent: QWidget | None = None):
        super().__init__("Runtime", parent)
        self.project_root_edit = QLineEdit(str(project_root))
        self.testrun_edit = QLineEdit("vctc_ngxpro")
        self.status_label = QLabel("idle")
        self.vehicle_label = QLabel("-")
        self.sensor_list = QListWidget()
        self.sensor_list.setMaximumHeight(120)
        self.testrun_control_label = QLabel("-")
        self.process_label = QLabel("-")
        self.output_dir_label = QLabel("-")
        self.probe_button = QPushButton("Probe Runtime")
        self.prepare_button = QPushButton("CM Prepare")
        self.browse_button = QPushButton("Browse")
        self.testrun_browse_button = QPushButton("Browse")

        self.browse_button.clicked.connect(self._browse_project_root)
        self.testrun_browse_button.clicked.connect(self._browse_testrun)

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
        form.addRow("Status", self.status_label)
        form.addRow("Vehicle", self.vehicle_label)
        form.addRow("Sensors", self.sensor_list)
        form.addRow("TestRun Control", self.testrun_control_label)
        form.addRow("Processes", self.process_label)
        form.addRow("Task Output", self.output_dir_label)

        action_row = QWidget(self)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(self.probe_button)
        action_layout.addWidget(self.prepare_button)

        cm_versions = detect_cm_versions()
        self.cm_version_combo = QComboBox()
        for ver in cm_versions:
            self.cm_version_combo.addItem(ver, cm_versions[ver])
        self._cm_install_dir: Path | None = None

        cm_row = QWidget(self)
        cm_layout = QHBoxLayout(cm_row)
        cm_layout.setContentsMargins(0, 0, 0, 0)
        cm_layout.addWidget(QLabel("CM Version:"))
        cm_layout.addWidget(self.cm_version_combo, 1)

        wrapper = QVBoxLayout(self)
        wrapper.addLayout(form)
        wrapper.addWidget(action_row)
        wrapper.addWidget(cm_row)
        wrapper.addStretch(1)

    @property
    def cm_install_path(self) -> Path | None:
        """
        Returns the full CM install directory for the selected version,
        or the directory from userdata if a custom version was typed.
        """
        data = self.cm_version_combo.currentData()
        if data is not None:
            return data  # type: ignore[return-value]
        text = self.cm_version_combo.currentText().strip()
        if not text:
            return None
        # Try to resolve as version string
        cm_versions = detect_cm_versions()
        if text in cm_versions:
            return cm_versions[text]
        return None

    def _browse_project_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Project Root", self.project_root_edit.text())
        if path:
            self.project_root_edit.setText(path)

    def _browse_testrun(self) -> None:
        testrun_dir = Path(self.project_root_edit.text()) / "Data" / "TestRun"
        path, _ = QFileDialog.getOpenFileName(
            self, "Select TestRun", str(testrun_dir), "TestRun files (*)")
        if path:
            self.testrun_edit.setText(path)

    def update_sensor_list(self, sensors: list[dict]) -> None:
        self.sensor_list.clear()
        for s in sensors:
            name = str(s.get("name", ""))
            active = bool(s.get("active", False))
            item = QListWidgetItem(name)
            if active:
                item.setForeground(_GREEN)
                item.setText(f"● {name}")
            else:
                item.setForeground(_GRAY)
                item.setText(f"○ {name}")
            item.setToolTip(f"active={active}")
            self.sensor_list.addItem(item)

    def clear_sensor_list(self) -> None:
        self.sensor_list.clear()

    def set_runtime_summary(self, payload: dict) -> None:
        self.status_label.setText(str(payload.get("status") or payload.get("mode") or "unknown"))
        self.vehicle_label.setText(str(payload.get("vehicle") or "-"))
        self.testrun_control_label.setText(str(payload.get("testrun_control") or "-"))
        counts = payload.get("process_counts") if isinstance(payload.get("process_counts"), dict) else {}
        if not counts:
            carmaker = payload.get("carmaker") if isinstance(payload.get("carmaker"), dict) else {}
            movie = payload.get("movie") if isinstance(payload.get("movie"), dict) else {}
            counts = {
                "carmaker": 1 if carmaker.get("pid") else 0,
                "gui_movie": 1 if movie.get("pid") else 0,
                "gpusensor_movie": 0,
            }
        self.process_label.setText(
            f"CM={counts.get('carmaker', 0)} GUI={counts.get('gui_movie', 0)} GPU={counts.get('gpusensor_movie', 0)}"
        )

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
        self.cm_version_combo.setEnabled(not locked)
        self.cm_version_combo.setToolTip(tip)
