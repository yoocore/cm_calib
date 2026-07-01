from __future__ import annotations

import sys

from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyleFactory

from src.gui_app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Camera Calibration Console")
    app.setOrganizationName("CMO141")

    # 显式设置Fusion样式，确保不同PySide6版本都保持一致的现代外观
    # Fusion样式：1) 支持圆角和自定义CSS 2) 不受系统主题影响 3) 跨平台一致
    app.setStyle(QStyleFactory.create("Fusion"))

    # 设置默认调色板（浅色主题），防止某些用户的系统深色主题导致黑色背景
    from PySide6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(255, 255, 255))
    palette.setColor(QPalette.WindowText, QColor(0, 0, 0))
    palette.setColor(QPalette.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
    palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(QPalette.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.Text, QColor(0, 0, 0))
    palette.setColor(QPalette.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ButtonText, QColor(0, 0, 0))
    palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.Link, QColor(0, 0, 255))
    palette.setColor(QPalette.Highlight, QColor(0, 120, 215))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    icon_path = Path(__file__).parent / "icon.svg"
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)

    default_root = Path.home()
    window = MainWindow(project_root=default_root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
