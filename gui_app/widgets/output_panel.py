from __future__ import annotations

import html
import os
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QTextCursor
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
_LOG_SOURCE_RE = re.compile(r"^\[(?P<source>[^\]]+)\]\s*(?P<body>.*)$")
_LOG_LEVEL_STYLES = {
    "info": {"label": "INFO", "fg": "#90caf9", "bg": "#0d1b2a"},
    "success": {"label": "SUCCESS", "fg": "#81c784", "bg": "#102417"},
    "warning": {"label": "WARNING", "fg": "#ffd54f", "bg": "#2a2111"},
    "error": {"label": "ERROR", "fg": "#ef9a9a", "bg": "#2b1416"},
}
_LOG_SOURCE_COLORS = {
    "runtime": "#64b5f6",
    "calibration": "#4dd0e1",
    "system": "#b0bec5",
}


def _format_score(value: float | None) -> str:
    return "-" if value is None else f"{value:.6f}"


def _normalize_log_source(source: str | None, line: str) -> tuple[str, str]:
    if source:
        return source.strip().lower() or "system", line.strip()

    text = line.strip()
    match = _LOG_SOURCE_RE.match(text)
    if match:
        parsed_source = match.group("source").strip().lower() or "system"
        parsed_body = match.group("body").strip()
        return parsed_source, parsed_body
    return "system", text


def _classify_log_level(message: str) -> str:
    text = message.casefold()
    if not text:
        return "info"
    if any(token in text for token in ("traceback", " exception", "failed", "fatal", "error", "critical")):
        return "error"
    if any(token in text for token in ("warn", "warning", "timeout", "timed out", "passive", "not ready", "mismatch")):
        return "warning"
    if any(token in text for token in (" success", " succeeded", "completed", "ready", " all passed", " ok", " status=ok")):
        return "success"
    return "info"


def _build_log_plain_text(timestamp_text: str, source: str, level: str, message: str) -> str:
    level_label = _LOG_LEVEL_STYLES[level]["label"]
    return f"[{timestamp_text}] [{source.upper()}] [{level_label}] {message}"


def _build_log_html(timestamp_text: str, source: str, level: str, message: str) -> str:
    level_style = _LOG_LEVEL_STYLES[level]
    source_color = _LOG_SOURCE_COLORS.get(source, "#b0bec5")
    return (
        '<div style="margin:0 0 4px 0;">'
        f'<span style="color:#8fa3b0;">[{html.escape(timestamp_text)}]</span> '
        f'<span style="color:{source_color}; font-weight:700;">[{html.escape(source.upper())}]</span> '
        f'<span style="color:{level_style["fg"]}; background-color:{level_style["bg"]}; '
        'font-weight:700; border-radius:4px; padding:1px 6px;">'
        f'[{html.escape(level_style["label"])}]</span> '
        f'<span style="color:#e6edf3;">{html.escape(message)}</span>'
        "</div>"
    )


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
        self.status_value.setText("fail" if result.status == "failed" else result.status)
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
        self.log_view.setStyleSheet(
            "QTextEdit {"
            "background-color: #0b1220;"
            "color: #e6edf3;"
            "border: 1px solid #334155;"
            "font-family: Consolas, 'Courier New', monospace;"
            "}"
        )

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

        layout = QVBoxLayout(self)
        layout.addWidget(top_row)
        layout.addWidget(self.results_scroll, 3)
        layout.addWidget(log_row)
        layout.addWidget(self.log_view, 1)

        self.open_output_button.clicked.connect(self._open_output_dir)
        self.open_log_button.clicked.connect(self._open_log_file)
        self.result_tree.itemSelectionChanged.connect(self._refresh_selection)

    def set_output_dir(self, output_dir: str | None) -> None:
        self.output_dir_label.setText(output_dir or "-")
        self.open_output_button.setEnabled(bool(output_dir))

    def set_log_path(self, log_path: str | None) -> None:
        self._task_log_path = str(log_path).strip() if log_path else None
        if self.result_tree.currentItem() is None:
            self._refresh_log_row()

    def append_log(self, line: str, *, source: str | None = None) -> None:
        parsed_source, message = _normalize_log_source(source, line)
        if not message:
            return
        level = _classify_log_level(message)
        timestamp_text = datetime.now().strftime("%H:%M:%S")
        plain_text = _build_log_plain_text(timestamp_text, parsed_source, level, message)
        entry_html = _build_log_html(timestamp_text, parsed_source, level, message)

        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(entry_html)
        cursor.insertBlock()
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

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

    def current_log_path(self) -> str | None:
        item = self.result_tree.currentItem()
        live_log = self._item_data(item, LIVE_LOG_ROLE) if item is not None else None
        return live_log or self._task_log_path

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
        self._refresh_selection()

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

    def _refresh_selection(self) -> None:
        item = self.result_tree.currentItem()
        if item is None:
            self._refresh_log_row()
            self._sync_card_selection(None)
            return

        camera_name = item.text(0) or "<unknown>"
        self._refresh_log_row(item)
        self._sync_card_selection(camera_name)

    def _sync_card_selection(self, selected_camera: str | None) -> None:
        for camera_name, card in self._result_cards.items():
            card.set_selected(camera_name == selected_camera)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.result_tree.currentItem() is not None:
            self._refresh_selection()

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
