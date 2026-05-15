from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QProgressBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class SensorProgressPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("Sensor Progress", parent)
        self.current_sensor_label = QLabel("Current Sensor: -")
        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setRange(0, 100)
        self.overall_progress_bar.setValue(0)
        self.overall_progress_detail_label = QLabel("0 / 0 | 0s / ~0s")
        self.sensor_progress_tree = QTreeWidget()
        self.sensor_progress_tree.setColumnCount(4)
        self.sensor_progress_tree.setHeaderLabels(
            ["Sensor", "Status", "Progress", "Elapsed / Est."]
        )
        self.sensor_progress_tree.header().setStretchLastSection(True)
        self._sensor_progress_items: dict[str, QTreeWidgetItem] = {}
        self._sensor_progress_bars: dict[str, QProgressBar] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(self.current_sensor_label)
        layout.addWidget(self.overall_progress_bar)
        layout.addWidget(self.overall_progress_detail_label)
        layout.addWidget(self.sensor_progress_tree)

    def reset_sensor_progress(
        self,
        cameras: list[str],
        estimated_per_camera: int,
        estimated_total: int,
    ) -> None:
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
            estimated_total_seconds=estimated_total,
        )

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
        display_status = "fail" if status == "failed" else status
        item.setText(1, display_status)
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
        self.current_sensor_label.setText(
            f"Current Sensor: {current_camera or '-'}"
        )
        self.overall_progress_bar.setValue(max(0, min(100, int(progress_percent))))
        self.overall_progress_detail_label.setText(
            f"{completed_count} / {total_count} | {self._format_duration(elapsed_seconds)} / ~{self._format_duration(estimated_total_seconds)}"
        )

    def _ensure_sensor_progress_item(
        self, camera_name: str, estimated_seconds: int
    ) -> QTreeWidgetItem:
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
