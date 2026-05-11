from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_app.models.state import CameraResult


RESULT_JSON_ROLE = Qt.UserRole + 1
BEST_IMAGE_ROLE = Qt.UserRole + 2
BEST_SCORE_IMAGE_ROLE = Qt.UserRole + 3
BEST_OVERLAY_IMAGE_ROLE = Qt.UserRole + 4
LIVE_LOG_ROLE = Qt.UserRole + 5
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _format_score(value: float | None) -> str:
    return "-" if value is None else f"{value:.6f}"


class ArtifactPreviewLabel(QLabel):
    clicked = Signal()

    def __init__(self, empty_text: str, parent: QWidget | None = None):
        super().__init__(empty_text, parent)
        self._empty_text = empty_text
        self._artifact_path: str | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(180, 120)
        self.setWordWrap(True)
        self.setStyleSheet("border: 1px solid #666; padding: 4px;")

    def set_artifact(self, artifact_path: str | None) -> None:
        self._artifact_path = artifact_path.strip() if artifact_path else None
        self._render()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._artifact_path:
            self.clicked.emit()
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if not self._artifact_path:
            self.setPixmap(QPixmap())
            self.setText(self._empty_text)
            return

        path = Path(self._artifact_path)
        if path.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
            self.setPixmap(QPixmap())
            self.setText(path.name)
            return

        if not path.exists():
            self.setPixmap(QPixmap())
            self.setText(f"Missing\n{path.name}")
            return

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.setPixmap(QPixmap())
            self.setText(f"Preview failed\n{path.name}")
            return

        scaled = pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled)
        self.setText("")


class CameraResultCard(QGroupBox):
    selected = Signal(str)
    activated = Signal(str)

    def __init__(self, camera_name: str, parent: QWidget | None = None):
        super().__init__(camera_name, parent)
        self.camera_name = camera_name

        self.status_value = QLabel("pending")
        self.best_score_value = QLabel("-")
        self.current_iter_value = QLabel("-")
        self.score_preview = ArtifactPreviewLabel("Score view", self)
        self.overlay_preview = ArtifactPreviewLabel("Overlap view", self)
        self.open_log_button = QPushButton("Log")
        self.open_result_button = QPushButton("Result JSON")
        self.open_best_button = QPushButton("Best")
        self.open_score_button = QPushButton("Score")
        self.open_overlay_button = QPushButton("Overlap")

        info_layout = QGridLayout()
        info_layout.addWidget(QLabel("Status"), 0, 0)
        info_layout.addWidget(self.status_value, 0, 1)
        info_layout.addWidget(QLabel("Best Score"), 1, 0)
        info_layout.addWidget(self.best_score_value, 1, 1)
        info_layout.addWidget(QLabel("Current Iter"), 2, 0)
        info_layout.addWidget(self.current_iter_value, 2, 1)

        previews = QWidget(self)
        previews_layout = QHBoxLayout(previews)
        previews_layout.setContentsMargins(0, 0, 0, 0)
        previews_layout.addWidget(self.score_preview, 1)
        previews_layout.addWidget(self.overlay_preview, 1)

        actions = QWidget(self)
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.addWidget(self.open_log_button)
        actions_layout.addWidget(self.open_result_button)
        actions_layout.addWidget(self.open_best_button)
        actions_layout.addWidget(self.open_score_button)
        actions_layout.addWidget(self.open_overlay_button)

        layout = QVBoxLayout(self)
        layout.addLayout(info_layout)
        layout.addWidget(previews)
        layout.addWidget(actions)

        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        border_color = "#1f6feb" if selected else "#666"
        self.setStyleSheet(f"QGroupBox {{ border: 2px solid {border_color}; margin-top: 8px; padding-top: 8px; }}")

    def update_result(self, result: CameraResult) -> None:
        self.status_value.setText(result.status)
        self.best_score_value.setText(_format_score(result.best_score))
        self.current_iter_value.setText(_format_score(result.current_iter_score))
        self.score_preview.set_artifact(result.best_score_image or result.best_image)
        self.overlay_preview.set_artifact(result.best_overlay_image or result.best_image)
        self.open_log_button.setEnabled(bool(result.live_log))
        self.open_result_button.setEnabled(bool(result.result_json))
        self.open_best_button.setEnabled(bool(result.best_image))
        self.open_score_button.setEnabled(bool(result.best_score_image or result.best_image))
        self.open_overlay_button.setEnabled(bool(result.best_overlay_image or result.best_image))

    def mousePressEvent(self, event) -> None:
        self.selected.emit(self.camera_name)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.selected.emit(self.camera_name)
        self.activated.emit(self.camera_name)
        super().mouseDoubleClickEvent(event)


class OutputPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("Output", parent)
        self.output_dir_label = QLabel("-")
        self.open_output_button = QPushButton("Open Output")
        self.open_output_button.setEnabled(False)
        self.log_path_label = QLabel("-")
        self.log_path_label.setWordWrap(True)
        self.open_log_button = QPushButton("Open Log File")
        self.open_log_button.setEnabled(False)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)

        self.result_tree = QTreeWidget(self)
        self.result_tree.setColumnCount(4)
        self.result_tree.setHeaderLabels(["Camera", "Status", "Best Score", "Current Iter Score"])
        self.result_tree.hide()

        self._task_log_path: str | None = None
        self._result_cards: dict[str, CameraResultCard] = {}

        self.results_container = QWidget(self)
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(10)
        self.results_layout.addStretch(1)

        self.results_scroll = QScrollArea(self)
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setWidget(self.results_container)

        self.preview_title_label = QLabel("Preview")
        self.preview_path_label = QLabel("Select a camera result to preview artifacts.")
        self.preview_path_label.setWordWrap(True)
        self.preview_image_label = QLabel("No preview")
        self.preview_image_label.setAlignment(Qt.AlignCenter)
        self.preview_image_label.setMinimumHeight(220)
        self.open_result_button = QPushButton("Open Result JSON")
        self.open_best_button = QPushButton("Open Best Image")
        self.open_score_button = QPushButton("Open Score View")
        self.open_overlay_button = QPushButton("Open Overlap View")
        for button in (
            self.open_result_button,
            self.open_best_button,
            self.open_score_button,
            self.open_overlay_button,
        ):
            button.setEnabled(False)

        top_row = QWidget(self)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self.output_dir_label, 1)
        top_layout.addWidget(self.open_output_button)

        log_row = QWidget(self)
        log_layout = QHBoxLayout(log_row)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(self.log_path_label, 1)
        log_layout.addWidget(self.open_log_button)

        preview_buttons = QWidget(self)
        preview_buttons_layout = QHBoxLayout(preview_buttons)
        preview_buttons_layout.setContentsMargins(0, 0, 0, 0)
        preview_buttons_layout.addWidget(self.open_result_button)
        preview_buttons_layout.addWidget(self.open_best_button)
        preview_buttons_layout.addWidget(self.open_score_button)
        preview_buttons_layout.addWidget(self.open_overlay_button)

        preview_panel = QWidget(self)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self.preview_title_label)
        preview_layout.addWidget(self.preview_path_label)
        preview_layout.addWidget(self.preview_image_label, 1)
        preview_layout.addWidget(preview_buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(top_row)
        layout.addWidget(self.results_scroll, 2)
        layout.addWidget(preview_panel, 1)
        layout.addWidget(log_row)
        layout.addWidget(self.log_view, 1)

        self.open_output_button.clicked.connect(self._open_output_dir)
        self.open_log_button.clicked.connect(self._open_log_file)
        self.result_tree.itemSelectionChanged.connect(self._refresh_preview)
        self.open_result_button.clicked.connect(lambda: self._open_current_artifact(RESULT_JSON_ROLE))
        self.open_best_button.clicked.connect(lambda: self._open_current_artifact(BEST_IMAGE_ROLE))
        self.open_score_button.clicked.connect(lambda: self._open_current_artifact(BEST_SCORE_IMAGE_ROLE))
        self.open_overlay_button.clicked.connect(lambda: self._open_current_artifact(BEST_OVERLAY_IMAGE_ROLE))

    def set_output_dir(self, output_dir: str | None) -> None:
        self.output_dir_label.setText(output_dir or "-")
        self.open_output_button.setEnabled(bool(output_dir))

    def set_log_path(self, log_path: str | None) -> None:
        self._task_log_path = str(log_path).strip() if log_path else None
        if self.result_tree.currentItem() is None:
            self._refresh_log_row()

    def append_log(self, line: str) -> None:
        self.log_view.append(line)

    def update_camera_result(self, result: CameraResult) -> None:
        item = self._find_or_create_item(result.camera)
        item.setText(0, result.camera)
        item.setText(1, result.status)
        item.setText(2, _format_score(result.best_score))
        item.setText(3, _format_score(result.current_iter_score))
        item.setData(0, LIVE_LOG_ROLE, result.live_log or "")
        item.setData(0, RESULT_JSON_ROLE, result.result_json or "")
        item.setData(0, BEST_IMAGE_ROLE, result.best_image or "")
        item.setData(0, BEST_SCORE_IMAGE_ROLE, result.best_score_image or "")
        item.setData(0, BEST_OVERLAY_IMAGE_ROLE, result.best_overlay_image or "")

        card = self._ensure_result_card(result.camera)
        card.update_result(result)

        if self.result_tree.currentItem() is None:
            self._select_camera(result.camera)
        elif self.result_tree.currentItem() is item or result.status in {"running", "finished", "failed"}:
            self._select_camera(result.camera)

    def resolve_item_artifact(self, item: QTreeWidgetItem, column: int) -> str | None:
        result_json = self._item_data(item, RESULT_JSON_ROLE)
        best_image = self._item_data(item, BEST_IMAGE_ROLE)
        best_score_image = self._item_data(item, BEST_SCORE_IMAGE_ROLE)
        best_overlay_image = self._item_data(item, BEST_OVERLAY_IMAGE_ROLE)
        candidates_by_column = {
            0: [result_json, best_image, best_score_image, best_overlay_image],
            1: [result_json, best_image, best_score_image, best_overlay_image],
            2: [best_score_image, best_image, result_json, best_overlay_image],
            3: [best_overlay_image, best_image, result_json, best_score_image],
        }
        for candidate in candidates_by_column.get(column, [result_json, best_image, best_score_image, best_overlay_image]):
            if candidate:
                return candidate
        return None

    def resolve_item_preview_artifact(self, item: QTreeWidgetItem) -> str | None:
        return self._first_existing_artifact(
            self._item_data(item, BEST_SCORE_IMAGE_ROLE),
            self._item_data(item, BEST_OVERLAY_IMAGE_ROLE),
            self._item_data(item, BEST_IMAGE_ROLE),
        )

    def current_log_path(self) -> str | None:
        item = self.result_tree.currentItem()
        return self._item_data(item, LIVE_LOG_ROLE) if item is not None else self._task_log_path

    def _ensure_result_card(self, camera_name: str) -> CameraResultCard:
        card = self._result_cards.get(camera_name)
        if card is not None:
            return card

        card = CameraResultCard(camera_name, self.results_container)
        card.selected.connect(self._select_camera)
        card.activated.connect(self._open_camera_default_artifact)
        card.open_log_button.clicked.connect(lambda _checked=False, name=camera_name: self._open_camera_artifact(name, LIVE_LOG_ROLE))
        card.open_result_button.clicked.connect(lambda _checked=False, name=camera_name: self._open_camera_artifact(name, RESULT_JSON_ROLE))
        card.open_best_button.clicked.connect(lambda _checked=False, name=camera_name: self._open_camera_artifact(name, BEST_IMAGE_ROLE))
        card.open_score_button.clicked.connect(lambda _checked=False, name=camera_name: self._open_camera_artifact(name, BEST_SCORE_IMAGE_ROLE))
        card.open_overlay_button.clicked.connect(lambda _checked=False, name=camera_name: self._open_camera_artifact(name, BEST_OVERLAY_IMAGE_ROLE))
        card.score_preview.clicked.connect(lambda name=camera_name: self._open_camera_artifact(name, BEST_SCORE_IMAGE_ROLE))
        card.overlay_preview.clicked.connect(lambda name=camera_name: self._open_camera_artifact(name, BEST_OVERLAY_IMAGE_ROLE))
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)
        self._result_cards[camera_name] = card
        return card

    def _find_or_create_item(self, camera_name: str) -> QTreeWidgetItem:
        item = self._find_item(camera_name)
        if item is not None:
            return item
        item = QTreeWidgetItem(self.result_tree)
        self.result_tree.addTopLevelItem(item)
        return item

    def _find_item(self, camera_name: str) -> QTreeWidgetItem | None:
        for index in range(self.result_tree.topLevelItemCount()):
            item = self.result_tree.topLevelItem(index)
            if item.text(0) == camera_name:
                return item
        return None

    def _select_camera(self, camera_name: str) -> None:
        item = self._find_item(camera_name)
        if item is None:
            return
        self.result_tree.setCurrentItem(item)
        self._refresh_preview()

    def _open_camera_artifact(self, camera_name: str, role: int) -> None:
        self._select_camera(camera_name)
        item = self._find_item(camera_name)
        artifact = self._item_data(item, role) if item is not None else None
        if artifact:
            os.startfile(artifact)

    def _open_camera_default_artifact(self, camera_name: str) -> None:
        self._select_camera(camera_name)
        item = self._find_item(camera_name)
        if item is None:
            return
        artifact = self._first_existing_artifact(
            self._item_data(item, RESULT_JSON_ROLE),
            self._item_data(item, BEST_IMAGE_ROLE),
            self._item_data(item, BEST_SCORE_IMAGE_ROLE),
            self._item_data(item, BEST_OVERLAY_IMAGE_ROLE),
        )
        if artifact:
            os.startfile(artifact)

    def _open_current_artifact(self, role: int) -> None:
        item = self.result_tree.currentItem()
        if item is None:
            return
        artifact = self._item_data(item, role)
        if artifact:
            os.startfile(artifact)

    def _refresh_preview(self) -> None:
        item = self.result_tree.currentItem()
        if item is None:
            self.preview_title_label.setText("Preview")
            self.preview_path_label.setText("Select a camera result to preview artifacts.")
            self.preview_image_label.setText("No preview")
            self.preview_image_label.setPixmap(QPixmap())
            self._set_artifact_buttons_enabled()
            self._refresh_log_row()
            self._sync_card_selection(None)
            return

        camera_name = item.text(0) or "<unknown>"
        preview_path = self.resolve_item_preview_artifact(item)
        self.preview_title_label.setText(f"Preview: {camera_name}")
        self.preview_path_label.setText(preview_path or (self._item_data(item, RESULT_JSON_ROLE) or "No artifact path"))
        self._update_preview_pixmap(preview_path)
        self._set_artifact_buttons_enabled(item)
        self._refresh_log_row(item)
        self._sync_card_selection(camera_name)

    def _sync_card_selection(self, selected_camera: str | None) -> None:
        for camera_name, card in self._result_cards.items():
            card.set_selected(camera_name == selected_camera)

    def _update_preview_pixmap(self, path_text: str | None) -> None:
        if not path_text:
            self.preview_image_label.setText("No preview")
            self.preview_image_label.setPixmap(QPixmap())
            return

        path = Path(path_text)
        if path.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
            self.preview_image_label.setText("Preview unavailable for non-image artifact")
            self.preview_image_label.setPixmap(QPixmap())
            return

        if not path.exists():
            self.preview_image_label.setText(f"Preview image not found\n{path_text}")
            self.preview_image_label.setPixmap(QPixmap())
            return

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.preview_image_label.setText(f"Failed to load preview\n{path_text}")
            self.preview_image_label.setPixmap(QPixmap())
            return

        scaled = pixmap.scaled(self.preview_image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_image_label.setPixmap(scaled)
        self.preview_image_label.setText("")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.result_tree.currentItem() is not None:
            self._refresh_preview()

    def _set_artifact_buttons_enabled(self, item: QTreeWidgetItem | None = None) -> None:
        current_item = item or self.result_tree.currentItem()
        self.open_result_button.setEnabled(bool(current_item and self._item_data(current_item, RESULT_JSON_ROLE)))
        self.open_best_button.setEnabled(bool(current_item and self._item_data(current_item, BEST_IMAGE_ROLE)))
        self.open_score_button.setEnabled(bool(current_item and self._item_data(current_item, BEST_SCORE_IMAGE_ROLE)))
        self.open_overlay_button.setEnabled(bool(current_item and self._item_data(current_item, BEST_OVERLAY_IMAGE_ROLE)))

    def _refresh_log_row(self, item: QTreeWidgetItem | None = None) -> None:
        current_item = item or self.result_tree.currentItem()
        live_log = self._item_data(current_item, LIVE_LOG_ROLE) if current_item is not None else None
        log_path = live_log or self._task_log_path
        self.log_path_label.setText(log_path or "-")
        self.open_log_button.setEnabled(bool(log_path))

    def _open_log_file(self) -> None:
        log_path = self.current_log_path()
        if log_path:
            os.startfile(log_path)

    @staticmethod
    def _first_existing_artifact(*candidates: str | None) -> str | None:
        first_non_empty: str | None = None
        for candidate in candidates:
            if not candidate:
                continue
            if first_non_empty is None:
                first_non_empty = candidate
            path = Path(candidate)
            if path.exists():
                return candidate
        return first_non_empty

    @staticmethod
    def _item_data(item: QTreeWidgetItem | None, role: int) -> str | None:
        if item is None:
            return None
        value = item.data(0, role)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _open_output_dir(self) -> None:
        text = self.output_dir_label.text().strip()
        if not text or text == "-":
            return
        os.startfile(text)
