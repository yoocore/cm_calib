from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QTreeWidget,
    QTreeWidgetItem,
)


class CalibrationPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("Calibration", parent)
        self.camera_list = QListWidget()
        self.camera_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.camera_list.setDefaultDropAction(Qt.MoveAction)
        self.camera_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.camera_list.setDragEnabled(True)
        self.camera_list.setAcceptDrops(True)
        self.camera_list.setDropIndicatorShown(True)
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
        self.generate_config_button = QPushButton("Generate Configs")
        self._generate_configs_ready = False
        self.generate_config_button.setEnabled(False)
        self.precheck_tree = QTreeWidget()
        self.precheck_tree.setColumnCount(3)
        self.precheck_tree.setHeaderLabels(["Camera", "Check", "Message"])
        self.estimate_label = QLabel("~ 0s (excluding CM Prepare)")
        self.phase_label = QLabel("")
        self.phase_label.setStyleSheet("color: #888; font-style: italic;")
        self.failure_summary = QTextEdit()
        self.failure_summary.setReadOnly(True)
        self.failure_summary.setPlaceholderText("Failures in prepare/start/stop will be summarized here.")
        self.failure_summary.setMinimumHeight(96)

        self.start_button = QPushButton("Calib Start")
        self.stop_button = QPushButton("Calib Stop")
        self.stop_button.setEnabled(False)

        form = QFormLayout()
        form.addRow("Campaign Rounds", self.campaign_rounds_spin)
        form.addRow("Multi-start Count", self.multi_start_count_spin)
        form.addRow("Multi-start Iters", self.multi_start_iters_spin)
        form.addRow("Refine Iters", self.refine_iters_spin)
        form.addRow("Jitter Steps", self.jitter_spin)
        form.addRow("Estimated Time", self.estimate_label)

        button_row = QWidget(self)
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)

        precheck_row = QWidget(self)
        precheck_layout = QHBoxLayout(precheck_row)
        precheck_layout.setContentsMargins(0, 0, 0, 0)
        precheck_layout.addWidget(self.precheck_button)
        precheck_layout.addWidget(self.generate_config_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.camera_list, 1)
        layout.addWidget(precheck_row)
        layout.addWidget(self.precheck_tree, 1)
        layout.addLayout(form)
        layout.addWidget(self.phase_label)
        layout.addWidget(self.explore_then_refine_check)
        layout.addWidget(self.resume_from_result_check)
        layout.addWidget(button_row)
        layout.addWidget(self.failure_summary)

        self.camera_list.itemChanged.connect(self._on_camera_selection_changed)
        self.camera_list.model().rowsMoved.connect(self._on_camera_rows_moved)
        self.campaign_rounds_spin.valueChanged.connect(lambda _value: self._update_estimated_time())
        self.multi_start_count_spin.valueChanged.connect(lambda _value: self._update_estimated_time())
        self.multi_start_iters_spin.valueChanged.connect(lambda _value: self._update_estimated_time())
        self.refine_iters_spin.valueChanged.connect(lambda _value: self._update_estimated_time())
        self.jitter_spin.valueChanged.connect(lambda _value: self._update_estimated_time())
        self.explore_then_refine_check.toggled.connect(lambda _checked: self._update_estimated_time())

        self._update_estimated_time()

    def set_cameras(self, cameras: list[str]) -> None:
        self.camera_list.clear()
        for camera_name in cameras:
            item = QListWidgetItem(camera_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.camera_list.addItem(item)
        self.clear_precheck_results()
        self._update_estimated_time()

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
            tooltip_lines = [
                *[str(path) for path in result.get("raw_matches", []) if path],
                *[str(path) for path in result.get("annotated_matches", []) if path],
            ]
            for key in ("config_path", "backup_path", "preview_path"):
                value = str(result.get(key) or "")
                if value:
                    tooltip_lines.append(value)
            item.setToolTip(2, "\n".join(tooltip_lines))
        self._generate_configs_ready = bool(results) and all(bool(result.get("ok")) for result in results)
        self.generate_config_button.setEnabled(self._generate_configs_ready)

    def clear_precheck_results(self) -> None:
        self.precheck_tree.clear()
        self._generate_configs_ready = False
        self.generate_config_button.setEnabled(False)

    def set_inputs_locked(self, locked: bool) -> None:
        self.camera_list.setEnabled(not locked)
        self.campaign_rounds_spin.setEnabled(not locked)
        self.multi_start_count_spin.setEnabled(not locked)
        self.multi_start_iters_spin.setEnabled(not locked)
        self.refine_iters_spin.setEnabled(not locked)
        self.jitter_spin.setEnabled(not locked)
        self.explore_then_refine_check.setEnabled(not locked)
        self.resume_from_result_check.setEnabled(not locked)
        self.precheck_button.setEnabled(not locked)
        self.generate_config_button.setEnabled((not locked) and self._generate_configs_ready)

    def set_failure_summary(self, text: str | None) -> None:
        self.failure_summary.setPlainText((text or "").strip())

    def clear_failure_summary(self) -> None:
        self.failure_summary.clear()

    def set_phase_label(self, text: str | None) -> None:
        self.phase_label.setText(text or "")

    def _on_camera_selection_changed(self, _item: QListWidgetItem) -> None:
        self.clear_precheck_results()
        self._update_estimated_time()

    def _on_camera_rows_moved(self, *_args) -> None:
        self.clear_precheck_results()
        self._update_estimated_time()

    def _update_estimated_time(self) -> None:
        camera_count = len(self.selected_cameras())
        campaign_rounds = int(self.campaign_rounds_spin.value())
        multi_start_count = int(self.multi_start_count_spin.value())
        multi_start_iters = int(self.multi_start_iters_spin.value()) or 30
        refine_iters = int(self.refine_iters_spin.value()) or 80

        if camera_count <= 0:
            self.estimate_label.setText("~ 0s (excluding CM Prepare)")
            return

        base_iter_count = refine_iters
        if self.explore_then_refine_check.isChecked():
            base_iter_count += multi_start_count * multi_start_iters
        else:
            base_iter_count += max(0, multi_start_count) * max(10, multi_start_iters // 2)

        per_camera_seconds = max(45, int(round(base_iter_count * 2.5 + float(self.jitter_spin.value()) * 8.0)))
        total_seconds = camera_count * campaign_rounds * per_camera_seconds
        self.estimate_label.setText(f"~ {self._format_duration(total_seconds)} (excluding CM Prepare)")

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        minutes, seconds = divmod(max(0, int(total_seconds)), 60)
        hours, minutes = divmod(minutes, 60)
        parts: list[str] = []
        if hours:
            parts.append(f"{hours}h")
        if hours or minutes:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)

