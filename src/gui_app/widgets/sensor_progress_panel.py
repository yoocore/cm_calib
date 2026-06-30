from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

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


class SensorProgressPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("Sensor Progress", parent)
        self.setStyleSheet(_PANEL_STYLE)
        self.current_sensor_label = QLabel("Current Sensor: -")
        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setRange(0, 100)
        self.overall_progress_bar.setValue(0)
        self.overall_progress_bar.setTextVisible(True)
        self.overall_progress_bar.setStyleSheet(
            "QProgressBar { text-align: center; border: 1px solid #cbd5e1; "
            "border-radius: 4px; background: #f1f5f9; min-height: 18px; }"
            "QProgressBar::chunk { background: #4fc3f7; border-radius: 3px; }"
        )
        self.overall_progress_detail_label = QLabel("0 / 0 | 0s / ~0s")
        self.sensor_progress_tree = QTreeWidget()
        self.sensor_progress_tree.setStyleSheet(
            "QTreeView { background-color: #ffffff; }"
            "QTreeView::item { background-color: #ffffff; padding: 2px 4px; }"
            "QTreeView::item:selected { background-color: #e8f0fe; }"
            "QHeaderView::section {"
            "border: none;"
            "border-bottom: 1px solid #cbd5e1;"
            "padding: 4px;"
            "color: #334155;"
            "background-color: transparent;"
            "}"
        )
        self.sensor_progress_tree.setColumnCount(8)
        self.sensor_progress_tree.setHeaderLabels(
            ["Sensor", "Status", "Iteration", "Elapsed", "Progress", "Init", "Current", "Best"]
        )
        header = self.sensor_progress_tree.header()
        header.setDefaultAlignment(Qt.AlignLeft)
        self.sensor_progress_tree.setIndentation(0)
        self._sensor_progress_items: dict[str, QTreeWidgetItem] = {}
        self._sensor_progress_bars: dict[str, QProgressBar] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        sensor_row = QHBoxLayout()
        sensor_row.addWidget(self.current_sensor_label, 1)
        sensor_row.addWidget(self.overall_progress_detail_label)
        layout.addLayout(sensor_row)
        layout.addWidget(self.overall_progress_bar)
        layout.addWidget(self.sensor_progress_tree, 1)

    def _setup_column_sizes(self) -> None:
        header = self.sensor_progress_tree.header()
        modes = [
            (0, QHeaderView.Interactive, 150),  # Sensor
            (1, QHeaderView.Interactive, 100),  # Status
            (2, QHeaderView.Interactive, 120),  # Iteration
            (3, QHeaderView.Interactive, 100),  # Elapsed
            (4, QHeaderView.Stretch, 150),      # Progress (bar)
            (5, QHeaderView.Interactive, 80),   # Init
            (6, QHeaderView.Interactive, 80),   # Current
            (7, QHeaderView.Interactive, 80),   # Best
        ]
        for col, mode, default_width in modes:
            header.setSectionResizeMode(col, mode)
            header.resizeSection(col, default_width)

    def reset_sensor_progress(
        self,
        cameras: list[str],
    ) -> None:
        self.sensor_progress_tree.clear()
        self._sensor_progress_items.clear()
        self._sensor_progress_bars.clear()
        for camera_name in cameras:
            self._ensure_sensor_progress_item(camera_name)
        self.set_overall_progress(
            current_camera=None,
            completed_count=0,
            total_count=len(cameras),
            progress_percent=0,
            elapsed_seconds=0,
        )

    def set_sensor_progress(
        self,
        camera_name: str,
        *,
        status: str,
        progress_percent: int,
        elapsed_seconds: int,
        detail: str | None = None,
        iter_text: str | None = None,
        init_score_text: str | None = None,
        current_score_text: str | None = None,
        best_score_text: str | None = None,
    ) -> None:
        item = self._ensure_sensor_progress_item(camera_name)
        display_status = "fail" if status == "failed" else status
        if display_status != item.text(1):
            item.setText(1, display_status)
        progress_bar = self._sensor_progress_bars[camera_name]
        progress_bar.setValue(max(0, min(100, int(progress_percent))))
        item.setText(3, self._format_duration(elapsed_seconds))
        if iter_text is not None:
            item.setText(2, iter_text)
        if init_score_text is not None:
            item.setText(5, init_score_text)
        if current_score_text is not None:
            item.setText(6, current_score_text)
        if best_score_text is not None:
            item.setText(7, best_score_text)

    def set_overall_progress(
        self,
        *,
        current_camera: str | None,
        completed_count: int,
        total_count: int,
        progress_percent: int,
        elapsed_seconds: int,
    ) -> None:
        self.current_sensor_label.setText(
            f"Current Sensor: {current_camera or '-'}"
        )
        self.overall_progress_bar.setValue(max(0, min(100, int(progress_percent))))
        self.overall_progress_detail_label.setText(
            f"{completed_count} / {total_count} | {self._format_duration(elapsed_seconds)}"
        )

    def _ensure_sensor_progress_item(
        self, camera_name: str
    ) -> QTreeWidgetItem:
        item = self._sensor_progress_items.get(camera_name)
        if item is not None:
            return item
        item = QTreeWidgetItem(self.sensor_progress_tree)
        item.setText(0, camera_name)
        item.setTextAlignment(0, Qt.AlignLeft | Qt.AlignVCenter)
        item.setText(1, "pending")
        item.setText(2, "")
        item.setText(3, "0s")
        item.setText(5, "")
        item.setText(6, "")
        item.setText(7, "")
        progress_bar = QProgressBar(self.sensor_progress_tree)
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        progress_bar.setTextVisible(True)
        progress_bar.setStyleSheet(
            "QProgressBar { text-align: center; border: 1px solid #cbd5e1; "
            "border-radius: 4px; background: #f1f5f9; min-height: 18px; }"
            "QProgressBar::chunk { background: #4fc3f7; border-radius: 3px; }"
        )
        self.sensor_progress_tree.setItemWidget(item, 4, progress_bar)
        self._sensor_progress_items[camera_name] = item
        self._sensor_progress_bars[camera_name] = progress_bar
        self._setup_column_sizes()
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
