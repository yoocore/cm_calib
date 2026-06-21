from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QCoreApplication
from PySide6.QtGui import (
    QImage,
    QPainter,
    QPen,
    QColor,
    QFont,
    QWheelEvent,
    QMouseEvent,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QComboBox,
    QSpinBox,
    QStackedWidget,
    QWidget,
    QFileDialog,
    QGroupBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QCheckBox,
    QTextEdit,
    QSplitter,
    QFormLayout,
    QMessageBox,
    QAbstractItemView,
    QHeaderView,
)

from gui_app.services.board_auto_detector import (
    BoardAutoDetector,
    DetectedBoard,
    DetectedTag,
    TagGrid,
    group_tags_into_grids,
    assign_checkerboard_ids,
    _bbox_iou,
    _bbox_from_points,
)
from gui_app.services.wizard_config_generator import (
    generate_config,
    generate_preview_image,
)


_BOARD_TYPE_COLORS = {
    "checkerboard": QColor(70, 80, 230),
    "aruco": QColor(220, 110, 60),
    "apriltag": QColor(60, 170, 90),
    "charuco": QColor(180, 60, 200),
    "circle_grid": QColor(60, 180, 200),
    "aruco_grid": QColor(200, 160, 60),
}

_IMAGE_SUFFIXES = "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"


class ImageCanvasWidget(QWidget):

    rectangle_drawn = Signal(int, int, int, int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._boards: List[DetectedBoard] = []
        self._tag_grids: List[TagGrid] = []
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._pan_start: Optional[QPointF] = None
        self._draw_mode = False
        self._draw_start: Optional[QPointF] = None
        self._draw_current: Optional[QPointF] = None
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)

    def set_image(self, image_path: str) -> None:
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            return
        self._pixmap = pixmap
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._fit_to_view()
        self.update()

    def set_detections(
        self,
        boards: List[DetectedBoard],
        tag_grids: Optional[List[TagGrid]] = None,
    ) -> None:
        self._boards = boards
        self._tag_grids = tag_grids or []
        self.update()

    def set_draw_mode(self, enabled: bool) -> None:
        self._draw_mode = enabled
        self._draw_start = None
        self._draw_current = None
        if enabled:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        self.update()

    def _screen_to_image(self, screen_pos: QPointF) -> QPointF:
        if self._zoom == 0:
            return QPointF(0, 0)
        img_x = (screen_pos.x() - self._offset.x()) / self._zoom
        img_y = (screen_pos.y() - self._offset.y()) / self._zoom
        return QPointF(img_x, img_y)

    def _fit_to_view(self) -> None:
        if not self._pixmap:
            return
        vw, vh = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return
        self._zoom = min(vw / pw, vh / ph) * 0.95
        self._offset = QPointF(
            (vw - pw * self._zoom) / 2,
            (vh - ph * self._zoom) / 2,
        )

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self._pixmap:
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        old_zoom = self._zoom
        self._zoom = max(0.05, min(20.0, self._zoom * factor))
        mouse_pos = event.position()
        self._offset = mouse_pos - (mouse_pos - self._offset) * (self._zoom / old_zoom)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            if self._draw_mode:
                self._draw_start = event.position()
                self._draw_current = event.position()
            else:
                self._pan_start = event.position()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._draw_mode and self._draw_start is not None:
            self._draw_current = event.position()
            self.update()
        elif not self._draw_mode and self._pan_start is not None:
            delta = event.position() - self._pan_start
            self._offset += delta
            self._pan_start = event.position()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._draw_mode and self._draw_start is not None:
            end_pos = event.position()
            img_start = self._screen_to_image(self._draw_start)
            img_end = self._screen_to_image(end_pos)
            x1, y1 = min(img_start.x(), img_end.x()), min(img_start.y(), img_end.y())
            x2, y2 = max(img_start.x(), img_end.x()), max(img_start.y(), img_end.y())
            w, h = x2 - x1, y2 - y1
            if w > 10 and h > 10:
                ix, iy = int(max(0, x1)), int(max(0, y1))
                iw, ih = int(w), int(h)
                self.rectangle_drawn.emit(ix, iy, iw, ih)
            self._draw_start = None
            self._draw_current = None
            self.update()
        self._pan_start = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._pixmap and self._zoom == 1.0:
            self._fit_to_view()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(40, 40, 40))

        if not self._pixmap:
            painter.setPen(QColor(180, 180, 180))
            painter.drawText(self.rect(), Qt.AlignCenter, "Load an image to begin")
            return

        painter.translate(self._offset)
        painter.scale(self._zoom, self._zoom)

        painter.drawPixmap(0, 0, self._pixmap)

        pen = QPen()
        pen.setWidthF(max(1.0, 2.0 / self._zoom))
        font = QFont()
        font.setPixelSize(max(12, int(18 / self._zoom)))

        for board in self._boards:
            color = _BOARD_TYPE_COLORS.get(board.board_type, QColor(200, 200, 70))
            pen.setColor(color)
            painter.setPen(pen)
            x, y, w, h = board.bbox
            painter.drawRect(QRectF(x, y, w, h))
            painter.setFont(font)
            painter.drawText(QPointF(x + 4, y - 6 / self._zoom), board.board_id)

        for grid in self._tag_grids:
            if any(b.board_id == grid.grid_id for b in self._boards):
                continue
            color = QColor(220, 110, 60)
            pen.setColor(color)
            painter.setPen(pen)
            x, y, w, h = grid.bbox
            painter.drawRect(QRectF(x, y, w, h))
            painter.setFont(font)
            painter.drawText(QPointF(x + 4, y - 6 / self._zoom), grid.grid_id)


        if self._draw_start is not None and self._draw_current is not None:
            img_start = self._screen_to_image(self._draw_start)
            img_end = self._screen_to_image(self._draw_current)
            x1 = min(img_start.x(), img_end.x())
            y1 = min(img_start.y(), img_end.y())
            x2 = max(img_start.x(), img_end.x())
            y2 = max(img_start.y(), img_end.y())
            dash_pen = QPen(QColor(255, 255, 0))
            dash_pen.setWidthF(max(1.0, 2.0 / self._zoom))
            dash_pen.setStyle(Qt.DashLine)
            painter.setPen(dash_pen)
            painter.drawRect(QRectF(x1, y1, x2 - x1, y2 - y1))

        painter.end()


class BoardListPanel(QWidget):
    board_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(["", "Use", "ID", "Type", "Size", "Points", "BBox"])
        header = self._table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setMinimumSectionSize(40)
        header.setStretchLastSection(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setStyleSheet(
            "QTableWidget::item:focus { outline: none; }"
            "QTableWidget::item:selected { background-color: #d0d0d0; color: black; }"
        )
        self._table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self._table)

        self._boards: List[DetectedBoard] = []
        self._unchecked_ids: set = set()
        self._suppress_events: bool = False

    def set_boards(self, boards: List[DetectedBoard]) -> None:
        self._suppress_events = True
        self._boards = list(boards)
        self._table.blockSignals(True)
        self._table.setRowCount(len(boards))
        for row, board in enumerate(boards):
            is_custom = board.board_type == "custom_maker"
            del_btn = QPushButton("−")
            del_btn.setFixedSize(24, 22)
            del_btn.setToolTip("Delete this board")
            del_btn.clicked.connect(lambda checked, r=row: self._delete_row_with_confirm(r))
            self._table.setCellWidget(row, 0, del_btn)

            cb = QCheckBox()
            cb.setChecked(board.board_id not in self._unchecked_ids)
            cb.stateChanged.connect(self._on_checkbox_changed)
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            cb_layout.addStretch()
            cb_layout.addWidget(cb)
            cb_layout.addStretch()
            self._table.setCellWidget(row, 1, cb_widget)

            for col, text in [
                (2, board.board_id),
                (3, board.board_type),
                (4, f"{board.board_size[0]}x{board.board_size[1]}" if board.board_size else "-"),
                (5, str(board.corners.shape[0] if board.corners.size > 0 else 0)),
                (6, str(board.bbox)),
            ]:
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                self._table.setItem(row, col, item)
        self._table.blockSignals(False)
        self._table.resizeColumnsToContents()
        for c in range(self._table.columnCount() - 1):
            self._table.setColumnWidth(c, self._table.columnWidth(c) + 16)
        self._suppress_events = False
        self.board_changed.emit()

    def get_active_boards(self) -> List[DetectedBoard]:
        active: List[DetectedBoard] = []
        for row, board in enumerate(self._boards):
            cb_container = self._table.cellWidget(row, 1)
            cb = cb_container.findChild(QCheckBox) if cb_container else None
            if cb and cb.isChecked():
                item = self._table.item(row, 2)
                board.board_id = item.text() if item else board.board_id
                active.append(board)
        return active

    def _on_cell_changed(self, row: int, col: int) -> None:
        if col == 2 and row < len(self._boards):
            item = self._table.item(row, col)
            if item:
                self._boards[row].board_id = item.text()
        self.board_changed.emit()

    _suppress_delete_confirm = False

    def _delete_row_with_confirm(self, row: int) -> None:
        if row < 0 or row >= len(self._boards):
            return
        board = self._boards[row]
        if board.board_type != "custom_maker" and not self._suppress_delete_confirm:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Question)
            msg_box.setWindowTitle("Delete Board")
            msg_box.setText(f"Delete auto-detected board {board.board_id}?")
            confirm_btn = msg_box.addButton("Confirm", QMessageBox.AcceptRole)
            cancel_btn = msg_box.addButton("Cancel", QMessageBox.RejectRole)
            skip_btn = msg_box.addButton("Don't ask again", QMessageBox.ActionRole)
            msg_box.setDefaultButton(cancel_btn)
            msg_box.exec()
            clicked = msg_box.clickedButton()
            if clicked == cancel_btn:
                return
            if clicked == skip_btn:
                BoardListPanel._suppress_delete_confirm = True

        self._boards.pop(row)
        self.set_boards(self._boards)
        self.board_changed.emit()

    def _on_checkbox_changed(self) -> None:
        if self._suppress_events:
            return
        self._unchecked_ids.clear()
        for row, board in enumerate(self._boards):
            cb_container = self._table.cellWidget(row, 1)
            cb = cb_container.findChild(QCheckBox) if cb_container else None
            if cb and not cb.isChecked():
                self._unchecked_ids.add(board.board_id)
        self.board_changed.emit()


class BootstrapWizardDialog(QDialog):

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        project_dir: Optional[str] = None,
        testrun: Optional[str] = None,
        camera_name: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
        )
        self.setWindowTitle("Board Calibration Wizard")
        self.resize(1200, 800)

        self._detector = BoardAutoDetector()
        self._image_path: str = ""
        self._boards: List[DetectedBoard] = []
        self._tag_grids: List[TagGrid] = []
        self._tags: List[DetectedTag] = []
        self._gui_project_dir = project_dir
        self._gui_testrun = testrun
        self._gui_camera_name = camera_name

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_input_page())
        self._stack.addWidget(self._build_review_page())
        self._stack.addWidget(self._build_output_page())

        layout = QVBoxLayout(self)
        layout.addWidget(self._stack)

    def _build_input_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        camera_group = QGroupBox("Camera Mapping (optional)")
        camera_form = QGridLayout(camera_group)
        camera_form.setColumnStretch(1, 1)

        self._project_dir_edit = QLineEdit()
        self._project_dir_edit.setPlaceholderText("Project root directory...")
        proj_browse = QPushButton("Browse...")
        proj_browse.clicked.connect(self._browse_project_dir)
        self._proj_dir_label = QLabel("ProjectDir:")
        camera_form.addWidget(self._proj_dir_label, 0, 0)
        camera_form.addWidget(self._project_dir_edit, 0, 1)
        camera_form.addWidget(proj_browse, 0, 2)

        self._testrun_edit = QLineEdit()
        self._testrun_edit.setPlaceholderText("TestRun path relative to Data/TestRun...")
        tr_browse = QPushButton("Browse...")
        tr_browse.clicked.connect(self._browse_testrun)
        self._testrun_label = QLabel("TestRun:")
        camera_form.addWidget(self._testrun_label, 1, 0)
        camera_form.addWidget(self._testrun_edit, 1, 1)
        camera_form.addWidget(tr_browse, 1, 2)

        self._camera_combo = QComboBox()
        self._camera_combo.setEnabled(False)
        self._camera_combo.addItem("(select camera)")
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh_camera_list)
        camera_form.addWidget(QLabel("Camera:"), 2, 0)
        camera_form.addWidget(self._camera_combo, 2, 1)
        camera_form.addWidget(self._refresh_btn, 2, 2)

        if self._gui_project_dir:
            self._project_dir_edit.setText(self._gui_project_dir)
            self._proj_dir_label.setVisible(False)
            self._project_dir_edit.setVisible(False)
            proj_browse.setVisible(False)
        if self._gui_testrun:
            self._testrun_edit.setText(self._gui_testrun)
            self._testrun_label.setVisible(False)
            self._testrun_edit.setVisible(False)
            tr_browse.setVisible(False)
        if self._gui_project_dir and self._gui_testrun:
            self._refresh_camera_list()
            if self._gui_camera_name:
                idx = self._camera_combo.findText(self._gui_camera_name)
                if idx >= 0:
                    self._camera_combo.setCurrentIndex(idx)
        if self._gui_camera_name:
            camera_group.setVisible(False)

        layout.addWidget(camera_group)

        file_group = QGroupBox("Reference Image")
        file_layout = QHBoxLayout(file_group)
        self._image_path_edit = QLineEdit()
        self._image_path_edit.setPlaceholderText("Select a real camera image...")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_image)
        file_layout.addWidget(self._image_path_edit)
        file_layout.addWidget(browse_btn)
        layout.addWidget(file_group)

        # Auto-fill reference image from existing mapping (GUI mode)
        if self._gui_camera_name and self._gui_project_dir:
            from gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
            _mp = mapping_path_for_project(self._gui_project_dir)
            if _mp.exists():
                _m = json.loads(_mp.read_text(encoding="utf-8"))
                _e = _m.get(self._gui_camera_name, {})
                _ri = _e.get("real_image", "")
                if _ri and os.path.isfile(_ri):
                    self._image_path_edit.setText(_ri)
                    self._image_path = _ri

        type_group = QGroupBox("Board Type (multi-select supported)")
        type_layout = QVBoxLayout(type_group)
        self._cb_checkerboard = QCheckBox("Checkerboard")
        self._cb_aruco = QCheckBox("ArUco")
        self._cb_apriltag = QCheckBox("AprilTag")
        self._cb_charuco = QCheckBox("CharUco")
        self._cb_circle_grid = QCheckBox("Circle Grid")
        self._cb_aruco_grid = QCheckBox("ArUco Grid Board")
        self._cb_custom = QCheckBox("Custom (manual)")
        self._cb_checkerboard.setChecked(True)
        for cb in (
            self._cb_checkerboard, self._cb_aruco, self._cb_apriltag,
            self._cb_charuco, self._cb_circle_grid, self._cb_aruco_grid,
        ):
            cb.stateChanged.connect(self._on_type_changed)
            type_layout.addWidget(cb)
        sep_line = QWidget()
        sep_line.setFixedHeight(8)
        type_layout.addWidget(sep_line)
        self._cb_custom.stateChanged.connect(self._on_type_changed)
        type_layout.addWidget(self._cb_custom)
        layout.addWidget(type_group)

        params_group = QGroupBox("Parameters (optional)")
        _grid = QGridLayout(params_group)
        _grid.setContentsMargins(8, 8, 8, 8)
        _grid.setHorizontalSpacing(6)
        _grid.setColumnStretch(2, 1)
        _grid.setColumnStretch(4, 1)
        _row = [0]

        def _add_size_row(label_text: str) -> tuple:
            label = QLabel(label_text)
            cols_spin = QSpinBox()
            cols_spin.setRange(0, 50)
            cols_spin.setValue(0)
            cols_spin.setSpecialValueText("auto")
            rows_spin = QSpinBox()
            rows_spin.setRange(0, 50)
            rows_spin.setValue(0)
            rows_spin.setSpecialValueText("auto")
            r = _row[0]
            _grid.addWidget(label, r, 0)
            _grid.addWidget(QLabel("Cols:"), r, 1)
            _grid.addWidget(cols_spin, r, 2)
            _grid.addWidget(QLabel("Rows:"), r, 3)
            _grid.addWidget(rows_spin, r, 4)
            _row[0] += 1
            return label, cols_spin, rows_spin

        def _add_combo_row(label_text: str, items: list) -> tuple:
            label = QLabel(label_text)
            combo = QComboBox()
            combo.addItems(items)
            r = _row[0]
            _grid.addWidget(label, r, 0)
            _grid.addWidget(combo, r, 1, 1, 4)
            _row[0] += 1
            return label, combo

        self._param_row_widgets: dict[str, list[QWidget]] = {}

        def _track_row(key: str) -> None:
            row_widgets: list[QWidget] = []
            r = _row[0] - 1
            for c in range(_grid.columnCount()):
                item = _grid.itemAtPosition(r, c)
                if item and item.widget():
                    row_widgets.append(item.widget())
            self._param_row_widgets[key] = row_widgets

        cb_label, self._cb_size_cols, self._cb_size_rows = _add_size_row("[Checkerboard] Board Size:")
        _track_row("checkerboard")
        aruco_label, self._aruco_size_cols, self._aruco_size_rows = _add_size_row("[ArUco Grid] Board Size:")
        _track_row("aruco_grid")
        charuco_label, self._charuco_size_cols, self._charuco_size_rows = _add_size_row("[CharUco] Board Size:")
        _track_row("charuco")
        cg_label, self._cg_size_cols, self._cg_size_rows = _add_size_row("[Circle Grid] Board Size:")
        _track_row("circle_grid")

        _DICT_ITEMS = [
            "DICT_4X4_50", "DICT_5X5_100", "DICT_6X6_100", "DICT_7X7_100",
        ]

        aruco_d_label, self._aruco_dict_combo = _add_combo_row("[ArUco] Dictionary:", _DICT_ITEMS)
        _track_row("aruco")
        charuco_d_label, self._charuco_dict_combo = _add_combo_row("[CharUco] Dictionary:", _DICT_ITEMS)
        _track_row("charuco_dict")
        aruco_grid_d_label, self._aruco_grid_dict_combo = _add_combo_row("[ArUco Grid] Dictionary:", _DICT_ITEMS)
        _track_row("aruco_grid_dict")

        self._tag_family_label, self._tag_family_combo = _add_combo_row(
            "[AprilTag] Family:",
            ["auto", "tagStandard41h12", "tag36h11", "tag25h9", "tag16h5"],
        )
        _track_row("apriltag")

        self._on_type_changed()
        layout.addWidget(params_group)

        self._detect_btn = QPushButton("Detect Boards")
        self._detect_btn.setMinimumHeight(40)
        self._detect_btn.clicked.connect(self._on_detect)
        layout.addWidget(self._detect_btn)
        layout.addStretch()

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        return page

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        splitter = QSplitter(Qt.Horizontal)
        self._canvas = ImageCanvasWidget()
        self._canvas.rectangle_drawn.connect(self._on_rectangle_drawn)
        splitter.addWidget(self._canvas)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._board_list = BoardListPanel()
        self._board_list.board_changed.connect(self._on_board_list_changed)
        right_layout.addWidget(QLabel("Detected Boards:"))
        right_layout.addWidget(self._board_list)

        splitter.addWidget(right_panel)
        splitter.setSizes([750, 350])
        layout.addWidget(splitter)

        btn_layout = QHBoxLayout()
        back_btn = QPushButton("< Back")
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        self._redetect_btn = QPushButton("Re-Detect")
        self._redetect_btn.setToolTip("Re-run detection with current settings")
        self._redetect_btn.clicked.connect(self._on_redetect)
        self._add_custom_btn = QPushButton("Add Custom Board")
        self._add_custom_btn.setCheckable(True)
        self._add_custom_btn.setToolTip(
            "Toggle draw mode: drag a rectangle on the image to mark a partially-visible board.\n"
            "It will be added as a custom_maker with template_match."
        )
        self._add_custom_btn.toggled.connect(self._on_toggle_draw_mode)
        next_btn = QPushButton("Next >")
        next_btn.clicked.connect(self._on_review_next)
        btn_layout.addWidget(back_btn)
        btn_layout.addWidget(self._redetect_btn)
        btn_layout.addWidget(self._add_custom_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(next_btn)
        layout.addLayout(btn_layout)

        return page

    def _build_output_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        output_group = QGroupBox("Output")
        output_layout = QFormLayout(output_group)

        tpl_layout = QHBoxLayout()
        self._template_edit = QLineEdit()
        self._template_edit.setPlaceholderText("(optional) Select a template config to inherit parameters...")
        tpl_browse = QPushButton("Browse...")
        tpl_browse.clicked.connect(self._browse_template)
        tpl_layout.addWidget(self._template_edit)
        tpl_layout.addWidget(tpl_browse)
        output_layout.addRow("Template:", tpl_layout)

        dir_layout = QHBoxLayout()
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText("Select output directory for config...")
        dir_browse = QPushButton("Browse...")
        dir_browse.clicked.connect(self._browse_output_dir)
        dir_layout.addWidget(self._output_dir_edit)
        dir_layout.addWidget(dir_browse)
        self._default_dir_btn = QPushButton("Default")
        self._default_dir_btn.setToolTip("Auto-fill to {ProjectDir}/Movie/calibtool_{CameraName}/")
        self._default_dir_btn.clicked.connect(self._set_default_output_dir)
        dir_layout.addWidget(self._default_dir_btn)
        output_layout.addRow("Output Dir:", dir_layout)

        self._camera_name_label = QLabel("-")
        output_layout.addRow("Camera Name:", self._camera_name_label)
        layout.addWidget(output_group)

        preview_group = QGroupBox("Config Preview (JSON)")
        preview_layout = QVBoxLayout(preview_group)
        self._json_preview = QTextEdit()
        self._json_preview.setReadOnly(True)
        self._json_preview.setFont(QFont("Consolas", 9))
        preview_layout.addWidget(self._json_preview)
        layout.addWidget(preview_group)

        btn_layout = QHBoxLayout()
        back_btn = QPushButton("< Back")
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        self._generate_btn = QPushButton("Generate Config")
        self._generate_btn.setMinimumHeight(40)
        self._generate_btn.clicked.connect(self._on_generate)
        btn_layout.addWidget(back_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self._generate_btn)
        layout.addLayout(btn_layout)

        self._result_label = QLabel("")
        layout.addWidget(self._result_label)

        return page

    def _browse_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Reference Image", "",
            f"Images ({_IMAGE_SUFFIXES})",
        )
        if path:
            self._image_path_edit.setText(path)

    def _browse_project_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Project Root")
        if path:
            self._project_dir_edit.setText(path)

    def _browse_testrun(self) -> None:
        project_dir = self._project_dir_edit.text().strip()
        start_dir = str(Path(project_dir) / "Data" / "TestRun") if project_dir else ""
        path, _ = QFileDialog.getOpenFileName(self, "Select TestRun", start_dir, "TestRun files (*)")
        if path and project_dir:
            testrun_root = Path(project_dir) / "Data" / "TestRun"
            try:
                rel = str(Path(path).relative_to(testrun_root))
                self._testrun_edit.setText(rel)
            except ValueError:
                self._testrun_edit.setText(path)

    def _refresh_camera_list(self) -> None:
        project_dir = self._project_dir_edit.text().strip()
        testrun = self._testrun_edit.text().strip()
        if not project_dir or not testrun:
            return
        self._camera_combo.clear()
        self._camera_combo.addItem("(select camera)")
        try:
            # Prefer mapping file when available (presence of mapping hides Refresh too)
            from gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
            mapping_path = mapping_path_for_project(project_dir)
            if mapping_path.exists():
                mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            else:
                mapping = {}
            if mapping:
                sensors = list(mapping.keys())
                self._refresh_btn.setVisible(False)
            else:
                self._refresh_btn.setVisible(True)
                from gui_app.services.static_vehicle_reader import resolve_vehicle_info
                info = resolve_vehicle_info(Path(project_dir), testrun)
                sensors = [s["name"] for s in info.get("sensors", [])]
            for name in sensors:
                self._camera_combo.addItem(name)
            self._camera_combo.setEnabled(len(sensors) > 0)
        except Exception as exc:
            self._camera_combo.addItem(f"Error: {exc}")
            self._camera_combo.setEnabled(False)

    def _browse_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Template Config", "",
            "JSON files (*.json)",
        )
        if path:
            self._template_edit.setText(path)

    def _write_camera_mapping(
        self, cam_name: str, config_folder: str, output_dir: Path,
    ) -> None:
        from gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
        project_dir = self._project_dir_edit.text().strip()
        if not project_dir:
            return
        mapping_path = mapping_path_for_project(project_dir)
        if mapping_path.exists():
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        else:
            mapping = {}
        entry = mapping.get(cam_name, {})
        entry["config_folder"] = config_folder
        if not entry.get("real_image") and self._image_path:
            entry["real_image"] = str(Path(self._image_path).resolve())
        mapping[cam_name] = entry
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=4), encoding="utf-8")

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if path:
            self._output_dir_edit.setText(path)

    def _set_default_output_dir(self) -> None:
        project_dir = self._project_dir_edit.text().strip()
        if not project_dir:
            QMessageBox.warning(self, "Missing Project", "Please set ProjectDir first.")
            return
        if self._gui_camera_name:
            cam_name = self._gui_camera_name
        else:
            cam_name = self._camera_combo.currentText()
            if not cam_name or cam_name == "(select camera)":
                QMessageBox.warning(self, "Missing Camera", "Please select a camera first.")
                return
        default_path = str(Path(project_dir) / "Movie" / f"calibtool_{cam_name}")
        self._output_dir_edit.setText(default_path)

    def _get_checked_types(self) -> List[str]:
        types: List[str] = []
        if self._cb_checkerboard.isChecked():
            types.append("checkerboard")
        if self._cb_aruco.isChecked():
            types.append("aruco")
        if self._cb_apriltag.isChecked():
            types.append("apriltag")
        if self._cb_charuco.isChecked():
            types.append("charuco")
        if self._cb_circle_grid.isChecked():
            types.append("circle_grid")
        if self._cb_aruco_grid.isChecked():
            types.append("aruco_grid")
        return types

    def _on_type_changed(self) -> None:
        if self._cb_custom.isChecked():
            for widgets in self._param_row_widgets.values():
                for w in widgets:
                    w.setVisible(False)
            return

        checked_types = set(self._get_checked_types())
        for key, widgets in self._param_row_widgets.items():
            base_type = key.split("_dict")[0]
            show = base_type in checked_types
            for w in widgets:
                w.setVisible(show)

    def _on_detect(self) -> None:
        image_path = self._image_path_edit.text().strip()
        if not image_path or not Path(image_path).exists():
            self._status_label.setText("Please select a valid image file.")
            return

        self._image_path = image_path

        if self._cb_custom.isChecked():
            self._boards = []
            self._tag_grids = []
            self._tags = []
            self._canvas.set_image(image_path)
            self._canvas.set_detections(self._boards, self._tag_grids)
            self._board_list.set_boards(self._boards)
            self._stack.setCurrentIndex(1)
            self._add_custom_btn.setChecked(True)
            self._status_label.setText("")
            return

        checked_types = self._get_checked_types()
        if not checked_types:
            self._status_label.setText("Please select at least one board type.")
            return

        self._detect_btn.setEnabled(False)
        self._detect_btn.setText("Detecting... please wait")
        self._status_label.setText(
            f"Loading image and detecting ({', '.join(checked_types)})..."
        )
        QCoreApplication.processEvents()

        try:
            img = cv2.imread(image_path)
            if img is None:
                self._status_label.setText("Failed to read image.")
                return

            self._boards = []
            self._tag_grids = []
            self._tags = []

            for board_type in checked_types:
                if board_type == "checkerboard":
                    cols = self._cb_size_cols.value()
                    rows = self._cb_size_rows.value()
                    sizes = [(cols, rows)] if cols > 0 and rows > 0 else None
                    boards = self._detector.detect_checkerboard_instances(img, sizes)
                    checkerboards = assign_checkerboard_ids(boards)
                    self._boards.extend(checkerboards)

                elif board_type == "aruco":
                    dictionary = self._aruco_dict_combo.currentText()
                    tags = self._detector.detect_aruco_tags(img, dictionary)
                    grids = group_tags_into_grids(tags)
                    self._tags.extend(tags)
                    self._tag_grids.extend(grids)
                    for idx, grid in enumerate(grids):
                        bbox = grid.bbox
                        corners = np.concatenate([t.corners for t in grid.tags], axis=0)
                        self._boards.append(DetectedBoard(
                            board_type="aruco",
                            bbox=bbox,
                            corners=corners,
                            board_id=f"ar_{idx + 1}",
                            tags=grid.tags,
                            center=grid.center,
                            area=float(bbox[2] * bbox[3]),
                        ))

                elif board_type == "apriltag":
                    family_text = self._tag_family_combo.currentText()
                    auto = family_text == "auto"
                    family = "tagStandard41h12" if auto else family_text
                    tags = self._detector.detect_apriltags(img, family, auto_family=auto)
                    grids = group_tags_into_grids(tags)
                    self._tags.extend(tags)
                    self._tag_grids.extend(grids)
                    for idx, grid in enumerate(grids):
                        bbox = grid.bbox
                        corners = np.concatenate([t.corners for t in grid.tags], axis=0)
                        self._boards.append(DetectedBoard(
                            board_type="apriltag",
                            bbox=bbox,
                            corners=corners,
                            board_id=f"at_{idx + 1}",
                            tags=grid.tags,
                            center=grid.center,
                            area=float(bbox[2] * bbox[3]),
                        ))

                elif board_type == "charuco":
                    cols = self._charuco_size_cols.value() or 7
                    rows = self._charuco_size_rows.value() or 5
                    dictionary = self._charuco_dict_combo.currentText()
                    detected = self._detector.detect_charuco_boards(
                        img, (cols, rows), dictionary,
                    )
                    for idx, board in enumerate(detected):
                        board.board_id = f"cc_{idx + 1}"
                    self._boards.extend(detected)

                elif board_type == "circle_grid":
                    cols = self._cg_size_cols.value() or 0
                    rows = self._cg_size_rows.value() or 0
                    sizes = [(cols, rows)] if cols > 0 and rows > 0 else None
                    boards = self._detector.detect_circle_grids(img, sizes)
                    for idx, board in enumerate(boards):
                        board.board_id = f"cg_{idx + 1}"
                    self._boards.extend(boards)

                elif board_type == "aruco_grid":
                    cols = self._aruco_size_cols.value() or 0
                    rows = self._aruco_size_rows.value() or 0
                    dictionary = self._aruco_grid_dict_combo.currentText()
                    sizes = [(cols, rows)] if cols > 0 and rows > 0 else None
                    boards = self._detector.detect_aruco_grids(img, dictionary, sizes)
                    for idx, board in enumerate(boards):
                        board.board_id = f"ag_{idx + 1}"
                    self._boards.extend(boards)

            if "checkerboard" in checked_types and any(t in checked_types for t in ("aruco", "apriltag")):
                cb_regions = []
                for b in self._boards:
                    if b.board_type == "checkerboard":
                        x, y, w, h = b.bbox
                        pad = max(w, h) * 0.15
                        cb_regions.append((x - pad, y - pad, x + w + pad, y + h + pad))

                def _is_on_checkerboard(cx: float, cy: float) -> bool:
                    return any(
                        x1 <= cx <= x2 and y1 <= cy <= y2
                        for x1, y1, x2, y2 in cb_regions
                    )

                def _grid_on_checkerboard(g: TagGrid) -> bool:
                    return any(_is_on_checkerboard(t.center[0], t.center[1]) for t in g.tags)

                self._boards = [
                    b for b in self._boards
                    if b.board_type not in ("aruco", "apriltag")
                    or not _is_on_checkerboard(b.center[0], b.center[1])
                ]
                self._tag_grids = [
                    g for g in self._tag_grids
                    if not _grid_on_checkerboard(g)
                ]
                self._tags = [
                    t for t in self._tags
                    if not _is_on_checkerboard(t.center[0], t.center[1])
                ]

            count = len(self._boards)
            tag_count = len(self._tags)
            if count == 0:
                self._status_label.setText(
                    f"No boards detected for types: {', '.join(checked_types)}. Try different parameters."
                )
                return

            extra = f" ({tag_count} tags)" if tag_count else ""
            self._status_label.setText(f"Found {count} board(s){extra}.")
            self._canvas.set_image(image_path)
            self._canvas.set_detections(self._boards, self._tag_grids)
            self._board_list.set_boards(self._boards)
            self._stack.setCurrentIndex(1)

        except Exception as exc:
            self._status_label.setText(f"Detection error: {exc}")
        finally:
            self._detect_btn.setEnabled(True)
            self._detect_btn.setText("Detect Boards")

    def _on_redetect(self) -> None:
        self._boards = []
        self._tag_grids = []
        self._tags = []
        self._add_custom_btn.setChecked(False)
        BoardListPanel._suppress_delete_confirm = False
        self._on_detect()
        if self._stack.currentIndex() != 1:
            self._stack.setCurrentIndex(1)

    def _on_board_list_changed(self) -> None:
        self._boards = list(self._board_list._boards)  # Sync: board list is the source of truth
        active = self._board_list.get_active_boards()
        self._canvas.set_detections(active, self._tag_grids)

    def _on_toggle_draw_mode(self, checked: bool) -> None:
        self._canvas.set_draw_mode(checked)
        if checked:
            self._add_custom_btn.setText("Draw Mode ON (drag rect)")
            self._add_custom_btn.setStyleSheet("background-color: #ffcc00; color: #333;")
        else:
            self._add_custom_btn.setText("Add Custom Board")
            self._add_custom_btn.setStyleSheet("")

    def _on_rectangle_drawn(self, x: int, y: int, w: int, h: int) -> None:
        if not self._image_path:
            return

        img = cv2.imread(self._image_path)
        if img is None:
            return

        img_h, img_w = img.shape[:2]
        x = max(0, min(x, img_w - 1))
        y = max(0, min(y, img_h - 1))
        w = min(w, img_w - x)
        h = min(h, img_h - y)
        if w < 20 or h < 20:
            return

        custom_idx = sum(1 for b in self._boards if b.board_type == "custom_maker") + 1

        corners = np.array([
            [x, y], [x + w, y], [x + w, y + h], [x, y + h],
        ], dtype=np.float32)

        board = DetectedBoard(
            board_type="custom_maker",
            bbox=(x, y, w, h),
            corners=corners,
            board_id=f"mk_{custom_idx}",
            center=((x + w / 2.0), (y + h / 2.0)),
            area=float(w * h),
            weight=0.8,
        )
        self._boards.append(board)
        self._board_list.set_boards(self._boards)

    def _on_review_next(self) -> None:
        if self._gui_camera_name:
            cam_name = self._gui_camera_name
        else:
            camera_selection = self._camera_combo.currentText()
            if camera_selection and camera_selection != "(select camera)":
                cam_name = camera_selection
            else:
                from gui_app.services.wizard_config_generator import _derive_camera_name
                cam_name = _derive_camera_name(self._image_path)
        self._camera_name_label.setText(cam_name)

        project_dir = self._project_dir_edit.text().strip()
        if project_dir and not self._output_dir_edit.text().strip():
            # Priority 1: auto-fill from existing mapping if path still valid
            from gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
            mapping_path = mapping_path_for_project(project_dir)
            if mapping_path.exists():
                _mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
                _entry = _mapping.get(cam_name, {})
                _old_cfg = _entry.get("config_folder", "")
                if _old_cfg and os.path.isdir(_old_cfg):
                    self._output_dir_edit.setText(_old_cfg)
            # Priority 2: fallback to Movie/
            if not self._output_dir_edit.text().strip():
                movie_dir = str(Path(project_dir) / "Movie")
                self._output_dir_edit.setText(movie_dir)

        self._update_json_preview()
        self._stack.setCurrentIndex(2)

    def _update_json_preview(self) -> None:
        active = self._board_list.get_active_boards()
        template = None
        tpl_path = self._template_edit.text().strip()
        if tpl_path and Path(tpl_path).exists():
            try:
                with open(tpl_path, "r", encoding="utf-8-sig") as f:
                    template = json.load(f)
            except Exception:
                pass

        output_dir = self._output_dir_edit.text().strip() or "."
        dummy_path = Path(output_dir) / f"camera.{self._camera_name_label.text()}.json"

        try:
            cfg = generate_config(
                boards=active,
                tag_grids=self._tag_grids,
                real_image_path=self._image_path,
                output_path=dummy_path,
                template_config=template,
                camera_name=self._camera_name_label.text(),
            )
            self._json_preview.setPlainText(
                json.dumps(cfg, ensure_ascii=False, indent=4)
            )
        except Exception as exc:
            self._json_preview.setPlainText(f"Error generating preview: {exc}")

    def _on_generate(self) -> None:
        output_dir = self._output_dir_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "Missing Output", "Please select an output directory.")
            return

        active = self._board_list.get_active_boards()
        if not active:
            QMessageBox.warning(self, "No Boards", "No boards selected for config generation.")
            return

        template = None
        tpl_path = self._template_edit.text().strip()
        if tpl_path and Path(tpl_path).exists():
            try:
                with open(tpl_path, "r", encoding="utf-8-sig") as f:
                    template = json.load(f)
            except Exception as exc:
                QMessageBox.warning(self, "Template Error", str(exc))
                return

        cam_name = self._camera_name_label.text()
        camera_output_dir = Path(output_dir) / f"calibtool_{cam_name}"
        # Avoid double-nesting when output_dir already ends with calibtool_{cam_name}
        # (e.g. from "Default" button or mapping auto-fill which use the full path)
        if Path(output_dir).name == f"calibtool_{cam_name}":
            camera_output_dir = Path(output_dir)
        # Block if the resolved path still contains redundant nesting
        resolved_parts = camera_output_dir.resolve().parts
        nested_name = f"calibtool_{cam_name}"
        for i in range(len(resolved_parts) - 1):
            if resolved_parts[i] == nested_name and resolved_parts[i + 1] == nested_name:
                QMessageBox.critical(
                    self, "Invalid Output Path",
                    f"Generated path contains redundant nesting:\n"
                    f"{camera_output_dir}\n\n"
                    f"The directory '{nested_name}' appears twice in a row.\n"
                    f"Please choose a different output directory.",
                )
                return

        # Warn if mapping already points to a different location
        project_dir = self._project_dir_edit.text().strip()
        if project_dir:
            from gui_app.widgets.camera_mapping_dialog import mapping_path_for_project
            mapping_path = mapping_path_for_project(project_dir)
            if mapping_path.exists():
                _mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
                _entry = _mapping.get(cam_name, {})
                _old_cfg = _entry.get("config_folder", "")
                if _old_cfg and os.path.isdir(_old_cfg) and _old_cfg != str(camera_output_dir):
                    reply = QMessageBox.warning(
                        self, "Mapping Overwrite",
                        f"This camera already has a mapping pointing to:\n{_old_cfg}\n\n"
                        f"New config will be saved to:\n{camera_output_dir}\n\n"
                        f"The old mapping entry will be replaced. Continue?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if reply != QMessageBox.Yes:
                        return

        if camera_output_dir.exists():
            reply = QMessageBox.question(
                self, "Folder Exists",
                f"Folder already exists:\n{camera_output_dir}\n\nReplace it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            shutil.rmtree(str(camera_output_dir))

        camera_output_dir.mkdir(parents=True, exist_ok=True)
        output_path = camera_output_dir / f"camera.{cam_name}.json"

        templates_dir = camera_output_dir / "templates"
        templates_dir.mkdir(exist_ok=True)
        for idx, board in enumerate(active, 1):
            if board.board_type == "custom_maker" and board.bbox:
                img = cv2.imread(self._image_path)
                x, y, w, h = board.bbox
                crop = img[y:y+h, x:x+w]
                template_path = templates_dir / f"custom_{idx}.png"
                cv2.imwrite(str(template_path), crop)
                board.template_image = str(template_path)

        try:
            cfg = generate_config(
                boards=active,
                tag_grids=self._tag_grids,
                real_image_path=self._image_path,
                output_path=output_path,
                template_config=template,
                camera_name=cam_name,
            )

            # Copy real image to config directory for self-containment
            real_image_src = Path(self._image_path)
            real_image_dst = camera_output_dir / real_image_src.name
            if real_image_src.resolve() != real_image_dst.resolve():
                shutil.copy2(str(real_image_src), str(real_image_dst))
            cfg["real_image"] = str(real_image_dst.resolve())

            # Add vehicle_writeback for calibration result write-back
            cfg.setdefault("vehicle_writeback", {}).update({
                "enabled": True,
                "sensor_name": cam_name,
            })

            # Re-save config with updated fields
            output_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")

            preview_path = camera_output_dir / f"wizard_preview_{cam_name}.png"
            generate_preview_image(active, self._tag_grids, self._image_path, preview_path)

            self._write_camera_mapping(cam_name, str(camera_output_dir), Path(output_dir))

            self._result_label.setText(
                f"Config saved: {output_path}\n"
                f"Preview image: {preview_path}\n"
                f"Boards: {len(cfg.get('boards', []))}"
            )
            self._result_label.setStyleSheet("color: #2a2; font-weight: bold;")
            QMessageBox.information(
                self, "Success",
                f"Config generated successfully!\n\n"
                f"Config: {output_path}\n"
                f"Preview: {preview_path}",
            )
        except Exception as exc:
            self._result_label.setText(f"Generation failed: {exc}")
            self._result_label.setStyleSheet("color: #c22;")
            QMessageBox.critical(self, "Generation Failed", str(exc))
