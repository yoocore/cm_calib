from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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

_PRIMARY_BUTTON_STYLE = (
    "QPushButton {"
    "background-color: #0f62fe;"
    "color: #ffffff;"
    "border: none;"
    "border-radius: 8px;"
    "padding: 8px 14px;"
    "font-weight: 700;"
    "}"
    "QPushButton:disabled {"
    "background-color: #94a3b8;"
    "color: #e2e8f0;"
    "}"
)

_SECONDARY_BUTTON_STYLE = (
    "QPushButton {"
    "background-color: #eef2ff;"
    "color: #1e3a8a;"
    "border: 1px solid #c7d2fe;"
    "border-radius: 8px;"
    "padding: 8px 14px;"
    "font-weight: 600;"
    "}"
    "QPushButton:disabled {"
    "background-color: #f8fafc;"
    "color: #94a3b8;"
    "border-color: #e2e8f0;"
    "}"
)

_TERTIARY_BUTTON_STYLE = (
    "QPushButton {"
    "background-color: #eef2ff;"
    "color: #1e3a8a;"
    "border: 1px solid #a5b4fc;"
    "border-radius: 8px;"
    "padding: 8px 14px;"
    "font-weight: 600;"
    "}"
    "QPushButton:disabled {"
    "background-color: #f8fafc;"
    "color: #94a3b8;"
    "border-color: #e2e8f0;"
    "}"
)

_DANGER_BUTTON_STYLE = (
    "QPushButton {"
    "background-color: #fff1f2;"
    "color: #b42318;"
    "border: 1px solid #fecdd3;"
    "border-radius: 8px;"
    "padding: 8px 14px;"
    "font-weight: 600;"
    "}"
    "QPushButton:disabled {"
    "background-color: #f8fafc;"
    "color: #94a3b8;"
    "border-color: #e2e8f0;"
    "}"
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
            "QGroupBox { border: 1px solid #dbe4ee; border-radius: 8px;"
            " margin-top: 8px; padding-top: 12px; font-weight: 600; background-color: #fbfdff; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #475569; }"
        )


class _SectionGroup(QGroupBox):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self.setStyleSheet(_SECTION_GROUP_STYLE)


class CalibrationPanel(QGroupBox):
    estimated_time_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Calibration", parent)
        self.setStyleSheet(_PANEL_STYLE)

        self.campaign_rounds_spin = QSpinBox()
        self.campaign_rounds_spin.setRange(1, 999)
        self.campaign_rounds_spin.setValue(1)

        # --- Multi-Start spinboxes (Tab 1) ---
        self.multi_start_count_spin = QSpinBox()
        self.multi_start_count_spin.setRange(0, 999)
        self.multi_start_count_spin.setValue(5)

        self.multi_start_iters_spin = QSpinBox()
        self.multi_start_iters_spin.setRange(0, 100000)
        self.multi_start_iters_spin.setSpecialValueText("default")
        self.multi_start_iters_spin.setValue(30)

        # --- Explore+Refine spinboxes (Tab 2, independent) ---
        self._er_count_spin = QSpinBox()
        self._er_count_spin.setRange(0, 999)
        self._er_count_spin.setValue(5)

        self._er_iters_spin = QSpinBox()
        self._er_iters_spin.setRange(0, 100000)
        self._er_iters_spin.setSpecialValueText("default")
        self._er_iters_spin.setValue(30)

        self._er_refine_iters_spin = QSpinBox()
        self._er_refine_iters_spin.setRange(0, 100000)
        self._er_refine_iters_spin.setSpecialValueText("default")
        self._er_refine_iters_spin.setValue(80)

        # --- Shared spinners ---
        self.jitter_auto_cb = QCheckBox("Auto")
        self.jitter_auto_cb.setChecked(True)
        self.jitter_spin = QDoubleSpinBox()
        self.jitter_spin.setRange(0.0, 999.0)
        self.jitter_spin.setValue(2.0)
        self.jitter_spin.setDecimals(2)
        self.jitter_spin.setEnabled(False)
        self.jitter_auto_cb.toggled.connect(lambda checked: self.jitter_spin.setEnabled(not checked))

        self.status_label = QLabel("idle")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setMinimumHeight(34)
        self.status_label.setMinimumWidth(120)

        self.phase_label = QLabel("")
        self.phase_label.setWordWrap(True)
        self.phase_label.hide()

        cm_versions = detect_cm_versions()
        self.cm_version_combo = QComboBox()
        self.cm_version_combo.addItem("Select CM version", None)
        if not cm_versions:
            self.cm_version_combo.setItemText(0, "No CM versions detected")
        else:
            for ver in cm_versions:
                self.cm_version_combo.addItem(ver, cm_versions[ver])
        self.cm_version_combo.setCurrentIndex(0)

        self.start_button = QPushButton("Calib Start")
        self.start_button.setDefault(True)
        self.start_button.setAutoDefault(True)
        self.start_button.setStyleSheet(_PRIMARY_BUTTON_STYLE)
        self.start_button.setMinimumHeight(42)
        self.stop_button = QPushButton("Calib Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet(_DANGER_BUTTON_STYLE)
        self.stop_button.setMinimumHeight(42)
        self.prepare_button = QPushButton("CM Prepare")
        self.prepare_button.setStyleSheet(_SECONDARY_BUTTON_STYLE)
        self.prepare_button.setMinimumHeight(36)

        # --- Build UI ---
        self.strategy_group = _SectionGroup("Campaign Rounds")
        rounds_inner = QVBoxLayout(self.strategy_group)
        rounds_inner.setContentsMargins(8, 4, 8, 4)
        rounds_inner.setSpacing(10)

        rounds_top = QHBoxLayout()
        rounds_top.addWidget(QLabel("Rounds"))
        rounds_top.addWidget(self.campaign_rounds_spin, 1)
        rounds_inner.addLayout(rounds_top)

        self.strategy_tabs = QTabWidget()

        # Tab 1: Multi-Start
        ms_page = QWidget()
        ms_layout = QVBoxLayout(ms_page)
        ms_layout.setContentsMargins(4, 4, 4, 4)
        ms_dir = QHBoxLayout()
        ms_dir.addWidget(QLabel("Directions"))
        ms_dir.addWidget(self.multi_start_count_spin, 1)
        ms_layout.addLayout(ms_dir)
        ms_iters = QHBoxLayout()
        ms_iters.addWidget(QLabel("Iters"))
        ms_iters.addWidget(self.multi_start_iters_spin, 1)
        ms_layout.addLayout(ms_iters)
        self.strategy_tabs.addTab(ms_page, "Multi-Start")

        # Tab 2: Explore + Refine
        er_page = QWidget()
        er_layout = QVBoxLayout(er_page)
        er_layout.setContentsMargins(4, 4, 4, 4)

        er_explore_group = _SubGroup("Explore")
        er_explore_inner = QVBoxLayout(er_explore_group)
        er_explore_inner.setContentsMargins(8, 4, 8, 4)
        er_dir = QHBoxLayout()
        er_dir.addWidget(QLabel("Directions"))
        er_dir.addWidget(self._er_count_spin, 1)
        er_explore_inner.addLayout(er_dir)
        er_iters = QHBoxLayout()
        er_iters.addWidget(QLabel("Iters"))
        er_iters.addWidget(self._er_iters_spin, 1)
        er_explore_inner.addLayout(er_iters)
        er_layout.addWidget(er_explore_group)

        er_refine_group = _SubGroup("Refine")
        er_refine_inner = QVBoxLayout(er_refine_group)
        er_refine_inner.setContentsMargins(8, 4, 8, 4)
        er_ref_iters = QHBoxLayout()
        er_ref_iters.addWidget(QLabel("Iters"))
        er_ref_iters.addWidget(self._er_refine_iters_spin, 1)
        er_refine_inner.addLayout(er_ref_iters)
        er_layout.addWidget(er_refine_group)

        self.strategy_tabs.addTab(er_page, "Explore + Refine")
        rounds_inner.addWidget(self.strategy_tabs)

        jitter_row = QHBoxLayout()
        jitter_row.addWidget(self.jitter_auto_cb)
        jitter_row.addWidget(self.jitter_spin, 1)
        rounds_inner.addLayout(jitter_row)

        self.control_group = _SectionGroup("Run Controls")
        control_layout = QVBoxLayout(self.control_group)
        control_layout.setContentsMargins(6, 2, 6, 4)
        control_layout.setSpacing(3)

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status"))
        status_row.addWidget(self.status_label, 1)

        cm_row = QWidget(self.control_group)
        cm_layout = QHBoxLayout(cm_row)
        cm_layout.setContentsMargins(0, 0, 0, 0)
        cm_layout.addWidget(QLabel("CarMaker"))
        cm_layout.addWidget(self.cm_version_combo, 1)

        button_row = QWidget(self.control_group)
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(6)
        layout.addWidget(self.strategy_group)
        control_layout.addWidget(cm_row)
        control_layout.addLayout(status_row)
        control_layout.addWidget(self.prepare_button)
        control_layout.addWidget(self.phase_label)
        control_layout.addWidget(button_row)
        layout.addWidget(self.control_group)

        self.strategy_tabs.currentChanged.connect(
            lambda _i: self._on_estimated_time_changed()
        )
        self.campaign_rounds_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self.multi_start_count_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self.multi_start_iters_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self._er_count_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self._er_iters_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self._er_refine_iters_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )
        self.jitter_spin.valueChanged.connect(
            lambda _v: self._on_estimated_time_changed()
        )

        self.set_status("idle")

    @property
    def explore_then_refine(self) -> bool:
        return self.strategy_tabs.currentIndex() == 1

    @property
    def cm_install_path(self) -> Path | None:
        data = self.cm_version_combo.currentData()
        if isinstance(data, Path):
            return data
        return None

    def _on_estimated_time_changed(self) -> None:
        self.estimated_time_changed.emit()

    def estimated_per_camera_seconds(self) -> int:
        campaign_rounds = int(self.campaign_rounds_spin.value())
        jitter_val = 2.0 if self.jitter_auto_cb.isChecked() else float(self.jitter_spin.value())
        if self.explore_then_refine:
            start_count = max(0, int(self._er_count_spin.value()))
            explore_iters = int(self._er_iters_spin.value()) or 30
            refine_iters = int(self._er_refine_iters_spin.value()) or 80
            base = refine_iters + max(0, start_count) * max(10, explore_iters // 2)
        else:
            start_count = max(0, int(self.multi_start_count_spin.value()))
            multi_iters = int(self.multi_start_iters_spin.value()) or 30
            base = max(0, start_count) * max(10, multi_iters // 2)
        per_round = max(45, int(round(base * 3.5 + jitter_val * 8.0)))
        return max(1, campaign_rounds * per_round)

    def total_iterations_per_camera(self) -> int:
        campaign_rounds = int(self.campaign_rounds_spin.value())
        if self.explore_then_refine:
            start_count = max(0, int(self._er_count_spin.value()))
            explore_iters = int(self._er_iters_spin.value()) or 30
            refine_iters = int(self._er_refine_iters_spin.value()) or 80
            return max(1, campaign_rounds * (start_count * explore_iters + refine_iters))
        else:
            start_count = max(0, int(self.multi_start_count_spin.value()))
            multi_iters = int(self.multi_start_iters_spin.value()) or 30
            return max(1, campaign_rounds * start_count * multi_iters)

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
        phase_text = (text or "").strip()
        self.phase_label.setText(phase_text)
        self.phase_label.setVisible(bool(phase_text))

    def set_failure_summary(self, text: str | None) -> None:
        _ = text

    def clear_failure_summary(self) -> None:
        return

    def set_inputs_locked(self, locked: bool) -> None:
        self.campaign_rounds_spin.setEnabled(not locked)
        self.multi_start_count_spin.setEnabled(not locked)
        self.multi_start_iters_spin.setEnabled(not locked)
        self._er_count_spin.setEnabled(not locked)
        self._er_iters_spin.setEnabled(not locked)
        self._er_refine_iters_spin.setEnabled(not locked)
        self.jitter_spin.setEnabled(not locked and not self.jitter_auto_cb.isChecked())
        self.jitter_auto_cb.setEnabled(not locked)
        self.strategy_tabs.setEnabled(not locked)
        self.cm_version_combo.setEnabled(not locked)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(min(hint.width(), 380), hint.height())

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 360), hint.height())

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
