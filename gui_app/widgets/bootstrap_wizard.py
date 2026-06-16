from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
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
)

from gui_app.services.board_auto_detector import (
    BoardAutoDetector,
    DetectedBoard,
    DetectedTag,
    TagGrid,
    group_tags_into_grids,
    classify_checkerboards_by_size,
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
        self._drawn_rects: List[Tuple[int, int, int, int]] = []
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)

    def set_image(self, image_path: str) -> None:
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            return
        self._pixmap = pixmap
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._drawn_rects.clear()
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
                self._drawn_rects.append((ix, iy, iw, ih))
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

        custom_color = QColor(200, 50, 200)
        pen.setColor(custom_color)
        painter.setPen(pen)
        for rx, ry, rw, rh in self._drawn_rects:
            painter.drawRect(QRectF(rx, ry, rw, rh))

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
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(["Use", "ID", "Type", "Size", "Points", "BBox"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self._table)

        self._boards: List[DetectedBoard] = []

    def set_boards(self, boards: List[DetectedBoard]) -> None:
        self._boards = boards
        self._table.blockSignals(True)
        self._table.setRowCount(len(boards))
        for row, board in enumerate(boards):
            cb = QCheckBox()
            cb.setChecked(True)
            cb.stateChanged.connect(self._on_checkbox_changed)
            self._table.setCellWidget(row, 0, cb)
            self._table.setItem(row, 1, QTableWidgetItem(board.board_id))
            self._table.setItem(row, 2, QTableWidgetItem(board.board_type))
            size_text = f"{board.board_size[0]}x{board.board_size[1]}" if board.board_size else "-"
            self._table.setItem(row, 3, QTableWidgetItem(size_text))
            pts = board.corners.shape[0] if board.corners.size > 0 else 0
            self._table.setItem(row, 4, QTableWidgetItem(str(pts)))
            self._table.setItem(row, 5, QTableWidgetItem(str(board.bbox)))
        self._table.blockSignals(False)

    def get_active_boards(self) -> List[DetectedBoard]:
        active: List[DetectedBoard] = []
        for row, board in enumerate(self._boards):
            cb = self._table.cellWidget(row, 0)
            if isinstance(cb, QCheckBox) and cb.isChecked():
                item = self._table.item(row, 1)
                board.board_id = item.text() if item else board.board_id
                active.append(board)
        return active

    def _on_cell_changed(self, row: int, col: int) -> None:
        if col == 1 and row < len(self._boards):
            item = self._table.item(row, col)
            if item:
                self._boards[row].board_id = item.text()
        self.board_changed.emit()

    def _on_checkbox_changed(self) -> None:
        self.board_changed.emit()


class BootstrapWizardDialog(QDialog):

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Board Calibration Wizard")
        self.resize(1200, 800)

        self._detector = BoardAutoDetector()
        self._image_path: str = ""
        self._boards: List[DetectedBoard] = []
        self._tag_grids: List[TagGrid] = []
        self._tags: List[DetectedTag] = []

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_input_page())
        self._stack.addWidget(self._build_review_page())
        self._stack.addWidget(self._build_output_page())

        layout = QVBoxLayout(self)
        layout.addWidget(self._stack)

    def _build_input_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        file_group = QGroupBox("Reference Image")
        file_layout = QHBoxLayout(file_group)
        self._image_path_edit = QLineEdit()
        self._image_path_edit.setPlaceholderText("Select a real camera image...")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_image)
        file_layout.addWidget(self._image_path_edit)
        file_layout.addWidget(browse_btn)
        layout.addWidget(file_group)

        type_group = QGroupBox("Board Type")
        type_layout = QVBoxLayout(type_group)
        self._type_group = QButtonGroup(self)
        self._rb_checkerboard = QRadioButton("Checkerboard")
        self._rb_aruco = QRadioButton("ArUco")
        self._rb_apriltag = QRadioButton("AprilTag")
        self._rb_charuco = QRadioButton("CharUco")
        self._rb_circle_grid = QRadioButton("Circle Grid")
        self._rb_aruco_grid = QRadioButton("ArUco Grid Board")
        self._rb_checkerboard.setChecked(True)
        for rb in (
            self._rb_checkerboard, self._rb_aruco, self._rb_apriltag,
            self._rb_charuco, self._rb_circle_grid, self._rb_aruco_grid,
        ):
            self._type_group.addButton(rb)
            type_layout.addWidget(rb)
        layout.addWidget(type_group)

        params_group = QGroupBox("Parameters (optional)")
        params_layout = QFormLayout(params_group)
        self._aruco_dict_combo = QComboBox()
        self._aruco_dict_combo.addItems([
            "DICT_4X4_50", "DICT_5X5_100", "DICT_6X6_100", "DICT_7X7_100",
        ])
        params_layout.addRow("ArUco Dictionary:", self._aruco_dict_combo)

        self._tag_family_combo = QComboBox()
        self._tag_family_combo.addItems([
            "auto", "tagStandard41h12", "tag36h11", "tag25h9", "tag16h5",
        ])
        params_layout.addRow("AprilTag Family:", self._tag_family_combo)

        self._board_size_cols = QSpinBox()
        self._board_size_cols.setRange(0, 50)
        self._board_size_cols.setValue(0)
        self._board_size_cols.setSpecialValueText("auto")
        self._board_size_rows = QSpinBox()
        self._board_size_rows.setRange(0, 50)
        self._board_size_rows.setValue(0)
        self._board_size_rows.setSpecialValueText("auto")
        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel("Cols:"))
        size_layout.addWidget(self._board_size_cols)
        size_layout.addWidget(QLabel("Rows:"))
        size_layout.addWidget(self._board_size_rows)
        params_layout.addRow("Board Size:", size_layout)
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

    def _browse_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Template Config", "",
            "JSON files (*.json)",
        )
        if path:
            self._template_edit.setText(path)

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if path:
            self._output_dir_edit.setText(path)

    def _get_selected_board_type(self) -> str:
        if self._rb_aruco.isChecked():
            return "aruco"
        if self._rb_apriltag.isChecked():
            return "apriltag"
        if self._rb_charuco.isChecked():
            return "charuco"
        if self._rb_circle_grid.isChecked():
            return "circle_grid"
        if self._rb_aruco_grid.isChecked():
            return "aruco_grid"
        return "checkerboard"

    def _on_detect(self) -> None:
        image_path = self._image_path_edit.text().strip()
        if not image_path or not Path(image_path).exists():
            self._status_label.setText("Please select a valid image file.")
            return

        self._image_path = image_path
        self._status_label.setText("Detecting...")
        board_type = self._get_selected_board_type()

        try:
            img = cv2.imread(image_path)
            if img is None:
                self._status_label.setText("Failed to read image.")
                return

            self._boards = []
            self._tag_grids = []
            self._tags = []

            if board_type == "checkerboard":
                cols = self._board_size_cols.value()
                rows = self._board_size_rows.value()
                sizes = [(cols, rows)] if cols > 0 and rows > 0 else None
                self._boards = self._detector.detect_checkerboard_instances(img, sizes)
                large, small = classify_checkerboards_by_size(self._boards)
                self._boards = large + small

            elif board_type == "aruco":
                dictionary = self._aruco_dict_combo.currentText()
                self._tags = self._detector.detect_aruco_tags(img, dictionary)
                self._tag_grids = group_tags_into_grids(self._tags)
                for grid in self._tag_grids:
                    bbox = grid.bbox
                    corners = np.concatenate([t.corners for t in grid.tags], axis=0)
                    self._boards.append(DetectedBoard(
                        board_type="aruco",
                        bbox=bbox,
                        corners=corners,
                        board_id=grid.grid_id,
                        tags=grid.tags,
                        center=grid.center,
                        area=float(bbox[2] * bbox[3]),
                    ))

            elif board_type == "apriltag":
                family_text = self._tag_family_combo.currentText()
                auto = family_text == "auto"
                family = "tagStandard41h12" if auto else family_text
                self._tags = self._detector.detect_apriltags(img, family, auto_family=auto)
                self._tag_grids = group_tags_into_grids(self._tags)
                for grid in self._tag_grids:
                    bbox = grid.bbox
                    corners = np.concatenate([t.corners for t in grid.tags], axis=0)
                    self._boards.append(DetectedBoard(
                        board_type="apriltag",
                        bbox=bbox,
                        corners=corners,
                        board_id=grid.grid_id,
                        tags=grid.tags,
                        center=grid.center,
                        area=float(bbox[2] * bbox[3]),
                    ))

            elif board_type == "charuco":
                cols = self._board_size_cols.value() or 7
                rows = self._board_size_rows.value() or 5
                dictionary = self._aruco_dict_combo.currentText()
                detected = self._detector.detect_charuco_boards(
                    img, (cols, rows), dictionary,
                )
                self._boards = detected

            elif board_type == "circle_grid":
                cols = self._board_size_cols.value() or 0
                rows = self._board_size_rows.value() or 0
                sizes = [(cols, rows)] if cols > 0 and rows > 0 else None
                self._boards = self._detector.detect_circle_grids(img, sizes)
                for idx, board in enumerate(self._boards):
                    board.board_id = f"CG{idx + 1}"

            elif board_type == "aruco_grid":
                cols = self._board_size_cols.value() or 0
                rows = self._board_size_rows.value() or 0
                dictionary = self._aruco_dict_combo.currentText()
                sizes = [(cols, rows)] if cols > 0 and rows > 0 else None
                self._boards = self._detector.detect_aruco_grids(img, dictionary, sizes)
                for idx, board in enumerate(self._boards):
                    board.board_id = f"AG{idx + 1}"

            count = len(self._boards)
            tag_count = len(self._tags)
            if count == 0:
                self._status_label.setText(
                    f"No {board_type} boards detected. Try different parameters."
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

    def _on_board_list_changed(self) -> None:
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

        crop = img[y:y + h, x:x + w]
        template_dir = Path(self._image_path).parent / "wizard_templates"
        template_dir.mkdir(parents=True, exist_ok=True)
        custom_idx = sum(1 for b in self._boards if b.board_type == "custom_maker") + 1
        template_name = f"custom_{custom_idx}.png"
        template_path = template_dir / template_name
        cv2.imwrite(str(template_path), crop)

        corners = np.array([
            [x, y], [x + w, y], [x + w, y + h], [x, y + h],
        ], dtype=np.float32)

        board = DetectedBoard(
            board_type="custom_maker",
            bbox=(x, y, w, h),
            corners=corners,
            board_id=f"C{custom_idx}",
            center=((x + w / 2.0), (y + h / 2.0)),
            area=float(w * h),
            weight=0.8,
        )
        board.template_image = str(template_path)
        self._boards.append(board)
        self._board_list.set_boards(self._boards)
        self._canvas.set_detections(self._boards, self._tag_grids)

    def _on_review_next(self) -> None:
        from gui_app.services.wizard_config_generator import _derive_camera_name
        cam_name = _derive_camera_name(self._image_path)
        self._camera_name_label.setText(cam_name)
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
        output_path = Path(output_dir) / f"camera.{cam_name}.json"

        try:
            cfg = generate_config(
                boards=active,
                tag_grids=self._tag_grids,
                real_image_path=self._image_path,
                output_path=output_path,
                template_config=template,
                camera_name=cam_name,
            )

            preview_path = output_path.parent / f"wizard_preview_{cam_name}.png"
            generate_preview_image(active, self._tag_grids, self._image_path, preview_path)

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
