from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

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
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self.setStyleSheet(
            "QGroupBox { border: 1px solid #555; border-radius: 4px;"
            " margin-top: 4px; padding-top: 12px; font-weight: normal; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )


class CalibrationPanel(QGroupBox):
    prepare_clicked = Signal()
    status_query_clicked = Signal()
    estimated_time_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Calibration", parent)

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

        self.status_label = QLabel("idle")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setMinimumHeight(34)
        self.status_label.setMinimumWidth(120)

        self.estimate_label = QLabel("~ 0s")
        self.phase_label = QLabel("")
        self.phase_label.hide()

        self.failure_summary = QTextEdit()
        self.failure_summary.setReadOnly(True)
        self.failure_summary.setPlaceholderText(
            "Failures in prepare/start/stop will be summarized here."
        )
        self.failure_summary.setMinimumHeight(96)
        self.failure_summary.hide()

        self.prepare_button = QPushButton("CM Prepare")
        self.prepare_button.clicked.connect(self.prepare_clicked.emit)

        self.status_query_button = QPushButton("Query Status")
        self.status_query_button.clicked.connect(self.status_query_clicked.emit)

        cm_versions = detect_cm_versions()
        self.cm_version_combo = QComboBox()
        self.cm_version_combo.addItem("请选择 CM 版本", None)
        if not cm_versions:
            self.cm_version_combo.setItemText(0, "未检测到 CM 版本")
        else:
            for ver in cm_versions:
                self.cm_version_combo.addItem(ver, cm_versions[ver])
        self.cm_version_combo.setCurrentIndex(0)

        self.start_button = QPushButton("Calib Start")
        self.stop_button = QPushButton("Calib Stop")
        self.stop_button.setEnabled(False)

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

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status"))
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.phase_label, 1)

        cm_row = QWidget(self)
        cm_layout = QHBoxLayout(cm_row)
        cm_layout.setContentsMargins(0, 0, 0, 0)
        cm_layout.addWidget(QLabel("CM Version:"))
        cm_layout.addWidget(self.cm_version_combo)
        cm_layout.addWidget(self.prepare_button)

        button_row = QWidget(self)
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)

        layout = QVBoxLayout(self)
        layout.addWidget(rounds_group)
        layout.addLayout(status_row)
        layout.addWidget(self.failure_summary, 1)
        layout.addWidget(cm_row)
        layout.addWidget(self.status_query_button)
        layout.addWidget(button_row)
        layout.addStretch(1)

        self.campaign_rounds_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self.multi_start_count_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self.multi_start_iters_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self.refine_iters_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self.jitter_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )

        self.set_status("idle")
        self._update_estimated_time()

    @property
    def cm_install_path(self) -> Path | None:
        data = self.cm_version_combo.currentData()
        if isinstance(data, Path):
            return data
        return None

    def _on_estimated_time_changed(self) -> None:
        self._update_estimated_time()
        self.estimated_time_changed.emit()

    def _update_estimated_time(self) -> None:
        per_camera = self.estimated_per_camera_seconds()
        if per_camera <= 0:
            self.estimate_label.setText("~ 0s")
        else:
            self.estimate_label.setText(f"~ {self._format_duration(per_camera)} / camera")

    def estimated_per_camera_seconds(self) -> int:
        campaign_rounds = int(self.campaign_rounds_spin.value())
        multi_start_count = int(self.multi_start_count_spin.value())
        multi_start_iters = int(self.multi_start_iters_spin.value()) or 30
        refine_iters = int(self.refine_iters_spin.value()) or 80
        base_iter_count = (
            refine_iters + max(0, multi_start_count) * max(10, multi_start_iters // 2)
        )
        per_round_seconds = max(
            45,
            int(
                round(
                    base_iter_count * 3.5 + float(self.jitter_spin.value()) * 8.0
                )
            ),
        )
        return max(1, campaign_rounds * per_round_seconds)

    def set_status(self, text: str | None) -> None:
        status_text = (text or "").strip() or "idle"
        style_key = status_text
        display_text = "fail" if status_text == "failed" else status_text
        border_color, background_color = _STATUS_BADGE_STYLES.get(
            style_key, ("#455a64", "#eceff1")
        )
        self.status_label.setText(display_text)
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

    def set_failure_summary(self, text: str | None) -> None:
        self.failure_summary.setPlainText((text or "").strip())

    def clear_failure_summary(self) -> None:
        self.failure_summary.clear()

    def set_inputs_locked(self, locked: bool) -> None:
        self.campaign_rounds_spin.setEnabled(not locked)
        self.multi_start_count_spin.setEnabled(not locked)
        self.multi_start_iters_spin.setEnabled(not locked)
        self.refine_iters_spin.setEnabled(not locked)
        self.jitter_spin.setEnabled(not locked)
        self.prepare_button.setEnabled(not locked)
        self.cm_version_combo.setEnabled(not locked)

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
