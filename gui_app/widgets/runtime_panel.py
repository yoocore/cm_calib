from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)


class RuntimePanel(QGroupBox):
    def __init__(self, project_root: Path, parent: QWidget | None = None):
        super().__init__("Runtime", parent)
        self.project_root_edit = QLineEdit(str(project_root))
        self.testrun_edit = QLineEdit("vctc_ngxpro")
        self.status_label = QLabel("idle")
        self.vehicle_label = QLabel("-")
        self.active_sensors_label = QLabel("-")
        self.process_label = QLabel("-")
        self.output_dir_label = QLabel("-")
        self.probe_button = QPushButton("Probe Runtime")
        self.prepare_button = QPushButton("CM Prepare")
        self.browse_button = QPushButton("Browse")

        self.browse_button.clicked.connect(self._browse_project_root)

        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.project_root_edit, 1)
        row_layout.addWidget(self.browse_button)

        form = QFormLayout()
        form.addRow("ProjectDir", row)
        form.addRow("TestRun", self.testrun_edit)
        form.addRow("Status", self.status_label)
        form.addRow("Vehicle", self.vehicle_label)
        form.addRow("Active Sensors", self.active_sensors_label)
        form.addRow("Processes", self.process_label)
        form.addRow("Task Output", self.output_dir_label)

        action_row = QWidget(self)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(self.probe_button)
        action_layout.addWidget(self.prepare_button)

        wrapper = QVBoxLayout(self)
        wrapper.addLayout(form)
        wrapper.addWidget(action_row)
        wrapper.addStretch(1)

    def _browse_project_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Project Root", self.project_root_edit.text())
        if path:
            self.project_root_edit.setText(path)

    def set_runtime_summary(self, payload: dict) -> None:
        self.status_label.setText(str(payload.get("status") or payload.get("mode") or "unknown"))
        self.vehicle_label.setText(str(payload.get("vehicle") or "-"))
        active_sensors = payload.get("active_sensors") or []
        self.active_sensors_label.setText(", ".join(str(item) for item in active_sensors) if active_sensors else "-")
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
        self.project_root_edit.setEnabled(not locked)
        self.testrun_edit.setEnabled(not locked)
        self.browse_button.setEnabled(not locked)

