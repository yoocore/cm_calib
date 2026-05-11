from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_app.models.state import CameraResult


class OutputPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("Output", parent)
        self.output_dir_label = QLabel("-")
        self.open_output_button = QPushButton("Open Output")
        self.open_output_button.setEnabled(False)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.result_tree = QTreeWidget()
        self.result_tree.setColumnCount(4)
        self.result_tree.setHeaderLabels(["Camera", "Status", "Best Score", "Current Iter Score"])

        top_row = QWidget(self)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self.output_dir_label, 1)
        top_layout.addWidget(self.open_output_button)

        layout = QVBoxLayout(self)
        layout.addWidget(top_row)
        layout.addWidget(self.result_tree, 1)
        layout.addWidget(self.log_view, 1)

        self.open_output_button.clicked.connect(self._open_output_dir)

    def set_output_dir(self, output_dir: str | None) -> None:
        self.output_dir_label.setText(output_dir or "-")
        self.open_output_button.setEnabled(bool(output_dir))

    def append_log(self, line: str) -> None:
        self.log_view.append(line)

    def update_camera_result(self, result: CameraResult) -> None:
        item = self._find_or_create_item(result.camera)
        item.setText(0, result.camera)
        item.setText(1, result.status)
        item.setText(2, "-" if result.best_score is None else f"{result.best_score:.6f}")
        item.setText(3, "-" if result.current_iter_score is None else f"{result.current_iter_score:.6f}")
        item.setToolTip(0, result.result_json or "")
        item.setToolTip(2, result.best_score_image or "")
        item.setToolTip(3, result.best_overlay_image or "")

    def _find_or_create_item(self, camera_name: str) -> QTreeWidgetItem:
        for index in range(self.result_tree.topLevelItemCount()):
            item = self.result_tree.topLevelItem(index)
            if item.text(0) == camera_name:
                return item
        item = QTreeWidgetItem(self.result_tree)
        self.result_tree.addTopLevelItem(item)
        return item

    def _open_output_dir(self) -> None:
        text = self.output_dir_label.text().strip()
        if not text or text == "-":
            return
        os.startfile(text)
