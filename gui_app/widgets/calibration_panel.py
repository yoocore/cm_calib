from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QTreeWidget,
    QTreeWidgetItem,
)

_GREEN = QBrush(QColor("#4caf50"))
_RED = QBrush(QColor("#e53935"))

_STATUS_BADGE_STYLES = {
    "idle": ("#546e7a", "#f4f7f9"),
    "preparing": ("#ef6c00", "#fff3e0"),
    "passive": ("#8d6e63", "#f6efe9"),
    "ready": ("#2e7d32", "#e8f5e9"),
    "running": ("#1565c0", "#e3f2fd"),
    "finished": ("#00897b", "#e0f2f1"),
    "failed": ("#c62828", "#ffebee"),
    "stopped": ("#6d4c41", "#efebe9"),
}

_CM_ROOTS = [
    "D:/IPG/carmaker",
    "C:/IPG/carmaker",
    "D:/IPG",
    "C:/IPG",
    "D:/CarMaker",
    "C:/CarMaker",
    "D:/Program Files/IPG/carmaker",
    "C:/Program Files/IPG/carmaker",
    "D:/Program Files/CarMaker",
    "C:/Program Files/CarMaker",
    "D:/Program Files (x86)/IPG/carmaker",
    "C:/Program Files (x86)/IPG/carmaker",
]


def detect_cm_versions() -> dict[str, Path]:
    versions: dict[str, Path] = {}
    for root in _CM_ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for entry in root_path.iterdir():
            if not entry.is_dir() or not entry.name.startswith("win64-"):
                continue
            found = False
            for sub in ("GUI", "bin"):
                for exe in ("CM_Office.exe", "CM.exe"):
                    if (entry / sub / exe).is_file():
                        version = entry.name[len("win64-"):]
                        versions[version] = entry
                        found = True
                        break
                if found:
                    break
    return dict(sorted(versions.items(), key=lambda x: x[0], reverse=True))


class _SubGroup(QGroupBox):
    """A styled sub-group for Explore/Refine sections."""
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self.setStyleSheet(
            "QGroupBox { border: 1px solid #555; border-radius: 4px;"
            " margin-top: 4px; padding-top: 12px; font-weight: normal; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )


class CalibrationPanel(QGroupBox):
    prepare_clicked = Signal()

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
        self.multi_start_count_spin.setValue(5)

        self.multi_start_iters_spin = QSpinBox()
        self.multi_start_iters_spin.setRange(0, 100000)
        self.multi_start_iters_spin.setSpecialValueText("default")
        self.multi_start_iters_spin.setValue(30)

        self.refine_iters_spin = QSpinBox()
        self.refine_iters_spin.setRange(0, 100000)
        self.refine_iters_spin.setSpecialValueText("default")
        self.refine_iters_spin.setValue(80)

        self.jitter_spin = QDoubleSpinBox()
        self.jitter_spin.setRange(0.0, 999.0)
        self.jitter_spin.setValue(2.0)
        self.jitter_spin.setDecimals(2)
        self.precheck_button = QPushButton("Check Inputs")
        self.generate_config_button = QPushButton("Generate Configs")
        self._generate_configs_ready = False
        self.generate_config_button.setEnabled(False)
        self.precheck_tree = QTreeWidget()
        self.precheck_tree.setColumnCount(4)
        self.precheck_tree.setHeaderLabels(["Camera", "Check", "Config", "Message"])
        self.precheck_tree.header().setStretchLastSection(True)
        self.status_label = QLabel("idle")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setMinimumHeight(34)
        self.status_label.setMinimumWidth(120)
        self.estimate_label = QLabel("~ 0s")
        self.phase_label = QLabel("")
        self.phase_label.hide()
        self.failure_summary = QTextEdit()
        self.failure_summary.setReadOnly(True)
        self.failure_summary.setPlaceholderText("Failures in prepare/start/stop will be summarized here.")
        self.failure_summary.setMinimumHeight(96)
        self.failure_summary.hide()
        self.current_sensor_label = QLabel("Current Sensor: -")
        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setRange(0, 100)
        self.overall_progress_bar.setValue(0)
        self.overall_progress_detail_label = QLabel("0 / 0 | 0s / ~0s")
        self.sensor_progress_tree = QTreeWidget()
        self.sensor_progress_tree.setColumnCount(4)
        self.sensor_progress_tree.setHeaderLabels(["Sensor", "Status", "Progress", "Elapsed / Est."])
        self.sensor_progress_tree.header().setStretchLastSection(True)
        self._sensor_progress_items: dict[str, QTreeWidgetItem] = {}
        self._sensor_progress_bars: dict[str, QProgressBar] = {}

        self.start_button = QPushButton("Calib Start")
        self.stop_button = QPushButton("Calib Stop")
        self.stop_button.setEnabled(False)

        self.prepare_button = QPushButton("CM Prepare")
        self.prepare_button.clicked.connect(self.prepare_clicked.emit)

        cm_versions = detect_cm_versions()
        self.cm_version_combo = QComboBox()
        self.cm_version_combo.addItem("请选择 CM 版本", None)
        if not cm_versions:
            self.cm_version_combo.setItemText(0, "未检测到 CM 版本")
        else:
            for ver in cm_versions:
                self.cm_version_combo.addItem(ver, cm_versions[ver])
        self.cm_version_combo.setCurrentIndex(0)

        # --- Config hierarchy ---
        rounds_group = _SubGroup("Campaign Rounds")
        rounds_inner = QVBoxLayout(rounds_group)
        rounds_inner.setContentsMargins(8, 4, 8, 4)

        rounds_top = QHBoxLayout()
        rounds_top.addWidget(QLabel("Rounds"))
        rounds_top.addWidget(self.campaign_rounds_spin, 1)
        rounds_inner.addLayout(rounds_top)

        explore_group = _SubGroup("Explore")
        explore_inner = QVBoxLayout(explore_group)
        explore_inner.setContentsMargins(8, 4, 8, 4)
        explore_dir = QHBoxLayout()
        explore_dir.addWidget(QLabel("Directions"))
        explore_dir.addWidget(self.multi_start_count_spin, 1)
        explore_iters = QHBoxLayout()
        explore_iters.addWidget(QLabel("Iters"))
        explore_iters.addWidget(self.multi_start_iters_spin, 1)
        explore_inner.addLayout(explore_dir)
        explore_inner.addLayout(explore_iters)
        rounds_inner.addWidget(explore_group)

        refine_group = _SubGroup("Refine")
        refine_inner = QVBoxLayout(refine_group)
        refine_inner.setContentsMargins(8, 4, 8, 4)
        refine_iters = QHBoxLayout()
        refine_iters.addWidget(QLabel("Iters"))
        refine_iters.addWidget(self.refine_iters_spin, 1)
        refine_inner.addLayout(refine_iters)
        rounds_inner.addWidget(refine_group)

        estimate_row = QHBoxLayout()
        estimate_row.addWidget(QLabel("Estimated Time"))
        estimate_row.addWidget(self.estimate_label, 1)
        rounds_inner.addLayout(estimate_row)

        progress_group = _SubGroup("Sensor Progress")
        progress_inner = QVBoxLayout(progress_group)
        progress_inner.setContentsMargins(8, 4, 8, 4)
        progress_inner.addWidget(self.current_sensor_label)
        progress_inner.addWidget(self.overall_progress_bar)
        progress_inner.addWidget(self.overall_progress_detail_label)
        progress_inner.addWidget(self.sensor_progress_tree)

        # --- Buttons ---
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

        cm_row = QWidget(self)
        cm_layout = QHBoxLayout(cm_row)
        cm_layout.setContentsMargins(0, 0, 0, 0)
        cm_layout.addWidget(self.prepare_button)
        cm_layout.addWidget(QLabel("CM Version:"))
        cm_layout.addWidget(self.cm_version_combo, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.camera_list, 1)
        layout.addWidget(precheck_row)
        layout.addWidget(self.precheck_tree, 1)
        layout.addWidget(rounds_group)
        layout.addWidget(progress_group, 1)
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status"))
        status_row.addWidget(self.status_label, 1)
        layout.addLayout(status_row)
        layout.addWidget(cm_row)
        layout.addWidget(button_row)

        self.camera_list.itemChanged.connect(self._on_camera_selection_changed)
        self.camera_list.model().rowsMoved.connect(self._on_camera_rows_moved)
        self.campaign_rounds_spin.valueChanged.connect(lambda _v: self._update_estimated_time())
        self.multi_start_count_spin.valueChanged.connect(lambda _v: self._update_estimated_time())
        self.multi_start_iters_spin.valueChanged.connect(lambda _v: self._update_estimated_time())
        self.refine_iters_spin.valueChanged.connect(lambda _v: self._update_estimated_time())
        self.jitter_spin.valueChanged.connect(lambda _v: self._update_estimated_time())

        self.set_status("idle")
        self._update_estimated_time()

    @property
    def cm_install_path(self) -> Path | None:
        data = self.cm_version_combo.currentData()
        if isinstance(data, Path):
            return data
        return None

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
            status_text = "✓" if ok else "✗"
            item.setText(1, status_text)
            item.setForeground(1, _GREEN if ok else _RED)
            msg = str(result.get("message") or "")
            item.setText(0, camera_name)
            item.setToolTip(0, camera_name)

            # Column 1: checkmark with short reason
            check_text = f"{status_text} {msg}" if not ok and msg else status_text
            item.setText(1, check_text)
            item.setToolTip(1, msg)

            # Column 2: config / backup / preview paths
            config_parts: list[str] = []
            for key in ("config_path", "backup_path", "preview_path"):
                v = str(result.get(key) or "")
                if v:
                    config_parts.append(v)
            config_text = "; ".join(config_parts) if config_parts else ""
            item.setText(2, config_text)
            if config_text:
                item.setToolTip(2, config_text)

            # Column 3: detailed message with image paths
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
        self._generate_configs_ready = bool(results) and all(bool(r.get("ok")) for r in results)
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
        self.precheck_button.setEnabled(not locked)
        self.generate_config_button.setEnabled((not locked) and self._generate_configs_ready)
        self.prepare_button.setEnabled(not locked)
        self.cm_version_combo.setEnabled(not locked)

    def set_failure_summary(self, text: str | None) -> None:
        self.failure_summary.setPlainText((text or "").strip())

    def clear_failure_summary(self) -> None:
        self.failure_summary.clear()

    def reset_sensor_progress(self) -> None:
        self._rebuild_sensor_progress_plan()

    def set_sensor_progress(
        self,
        camera_name: str,
        *,
        status: str,
        progress_percent: int,
        elapsed_seconds: int,
        estimated_seconds: int,
        detail: str | None = None,
    ) -> None:
        item = self._ensure_sensor_progress_item(camera_name, estimated_seconds)
        item.setText(1, status)
        progress_bar = self._sensor_progress_bars[camera_name]
        progress_bar.setValue(max(0, min(100, int(progress_percent))))
        duration_text = f"{self._format_duration(elapsed_seconds)} / ~{self._format_duration(estimated_seconds)}"
        if detail:
            duration_text += f" | {detail}"
        item.setText(3, duration_text)
        item.setToolTip(3, duration_text)

    def set_overall_progress(
        self,
        *,
        current_camera: str | None,
        completed_count: int,
        total_count: int,
        progress_percent: int,
        elapsed_seconds: int,
        estimated_total_seconds: int,
    ) -> None:
        self.current_sensor_label.setText(f"Current Sensor: {current_camera or '-'}")
        self.overall_progress_bar.setValue(max(0, min(100, int(progress_percent))))
        self.overall_progress_detail_label.setText(
            f"{completed_count} / {total_count} | {self._format_duration(elapsed_seconds)} / ~{self._format_duration(estimated_total_seconds)}"
        )

    def set_status(self, text: str | None) -> None:
        status_text = (text or "").strip() or "idle"
        border_color, background_color = _STATUS_BADGE_STYLES.get(status_text, ("#455a64", "#eceff1"))
        self.status_label.setText(status_text)
        self.status_label.setStyleSheet(
            "QLabel {"
            f"border: 2px solid {border_color};"
            "border-radius: 8px;"
            f"background-color: {background_color};"
            f"color: {border_color};"
            "font-weight: 700;"
            "padding: 6px 12px;"
            "}"
        )

    def set_phase_label(self, text: str | None) -> None:
        self.phase_label.setText(text or "")

    def _on_camera_selection_changed(self, _item: QListWidgetItem) -> None:
        self.clear_precheck_results()
        self._update_estimated_time()

    def _on_camera_rows_moved(self, *_args) -> None:
        self.clear_precheck_results()
        self._update_estimated_time()

    def _update_estimated_time(self) -> None:
        total_seconds = self.estimated_total_seconds()
        if total_seconds <= 0:
            self.estimate_label.setText("~ 0s")
        else:
            self.estimate_label.setText(f"~ {self._format_duration(total_seconds)}")
        self._rebuild_sensor_progress_plan()

    def estimated_total_seconds(self) -> int:
        camera_count = len(self.selected_cameras())
        if camera_count <= 0:
            return 0
        return camera_count * self.estimated_per_camera_seconds()

    def estimated_per_camera_seconds(self) -> int:
        campaign_rounds = int(self.campaign_rounds_spin.value())
        multi_start_count = int(self.multi_start_count_spin.value())
        multi_start_iters = int(self.multi_start_iters_spin.value()) or 30
        refine_iters = int(self.refine_iters_spin.value()) or 80
        base_iter_count = refine_iters + max(0, multi_start_count) * max(10, multi_start_iters // 2)
        per_round_seconds = max(45, int(round(base_iter_count * 3.5 + float(self.jitter_spin.value()) * 8.0)))
        return max(1, campaign_rounds * per_round_seconds)

    def _rebuild_sensor_progress_plan(self) -> None:
        cameras = self.selected_cameras()
        estimated_per_camera = self.estimated_per_camera_seconds() if cameras else 0
        self.sensor_progress_tree.clear()
        self._sensor_progress_items.clear()
        self._sensor_progress_bars.clear()
        for camera_name in cameras:
            self._ensure_sensor_progress_item(camera_name, estimated_per_camera)
        self.set_overall_progress(
            current_camera=None,
            completed_count=0,
            total_count=len(cameras),
            progress_percent=0,
            elapsed_seconds=0,
            estimated_total_seconds=self.estimated_total_seconds(),
        )

    def _ensure_sensor_progress_item(self, camera_name: str, estimated_seconds: int) -> QTreeWidgetItem:
        item = self._sensor_progress_items.get(camera_name)
        if item is not None:
            return item
        item = QTreeWidgetItem(self.sensor_progress_tree)
        item.setText(0, camera_name)
        item.setText(1, "pending")
        item.setText(3, f"0s / ~{self._format_duration(estimated_seconds)}")
        progress_bar = QProgressBar(self.sensor_progress_tree)
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        self.sensor_progress_tree.setItemWidget(item, 2, progress_bar)
        self._sensor_progress_items[camera_name] = item
        self._sensor_progress_bars[camera_name] = progress_bar
        return item

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
