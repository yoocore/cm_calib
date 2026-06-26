from __future__ import annotations

import sys

from pathlib import Path
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from src.gui_app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    app.setStyleSheet("""
        QPushButton { min-height: 28px; padding: 4px 14px; }
        QSpinBox { min-height: 24px; }
        QProgressBar { min-height: 6px; max-height: 6px; }
    """)
    app.setApplicationName("Camera Calibration Console")
    app.setOrganizationName("CMO141")
    default_root = Path.home()
    window = MainWindow(project_root=default_root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
