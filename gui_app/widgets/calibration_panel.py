from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
    QTreeWidget,
    QTreeWidgetItem,
)


class CalibrationPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("Calibration", parent)
        self.camera_list = QListWidget()
        self.campaign_rounds_spin = QSpinBox()
        self.campaign_rounds_spin.setRange(1, 999)
        self.campaign_rounds_spin.setValue(1)

        self.multi_start_count_spin = QSpinBox()
        self.multi_start_count_spin.setRange(0, 999)
        self.multi_start_count_spin.setValue(0)

        self.multi_start_iters_spin = QSpinBox()
        self.multi_start_iters_spin.setRange(0, 100000)
        self.multi_start_iters_spin.setSpecialValueText("default")
        self.multi_start_iters_spin.setValue(0)

        self.refine_iters_spin = QSpinBox()
        self.refine_iters_spin.setRange(0, 100000)
        self.refine_iters_spin.setSpecialValueText("default")
        self.refine_iters_spin.setValue(0)

        self.jitter_spin = QDoubleSpinBox()
        self.jitter_spin.setRange(0.0, 999.0)
        self.jitter_spin.setValue(2.0)
        self.jitter_spin.setDecimals(2)

        self.explore_then_refine_check = QCheckBox("Explore Then Refine")
        self.resume_from_result_check = QCheckBox("Resume From Result")
        self.precheck_button = QPushButton("Check Inputs")
        self.precheck_tree = QTreeWidget()
        self.precheck_tree.setColumnCount(3)
        self.precheck_tree.setHeaderLabels(["Camera", "Check", "Message"])

        self.start_button = QPushButton("Calib Start")
        self.stop_button = QPushButton("Calib Stop")
        self.stop_button.setEnabled(False)

        form = QFormLayout()
        form.addRow("Campaign Rounds", self.campaign_rounds_spin)
        form.addRow("Multi-start Count", self.multi_start_count_spin)
        form.addRow("Multi-start Iters", self.multi_start_iters_spin)
        form.addRow("Refine Iters", self.refine_iters_spin)
        form.addRow("Jitter Steps", self.jitter_spin)

        button_row = QWidget(self)
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.camera_list, 1)
        layout.addWidget(self.precheck_button)
        layout.addWidget(self.precheck_tree, 1)
        layout.addLayout(form)
        layout.addWidget(self.explore_then_refine_check)
        layout.addWidget(self.resume_from_result_check)
        layout.addWidget(button_row)

    def set_cameras(self, cameras: list[str]) -> None:
        self.camera_list.clear()
        for camera_name in cameras:
            item = QListWidgetItem(camera_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.camera_list.addItem(item)

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
            item.setText(1, "ok" if ok else "failed")
            item.setText(2, str(result.get("message") or ""))
            item.setToolTip(2, "\n".join([*result.get("raw_matches", []), *result.get("annotated_matches", [])]))

