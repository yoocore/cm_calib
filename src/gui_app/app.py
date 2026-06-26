from __future__ import annotations

import sys

from pathlib import Path
from PySide6.QtWidgets import QApplication

from src.gui_app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Camera Calibration Console")
    app.setOrganizationName("CMO141")
    default_root = Path.home()
    window = MainWindow(project_root=default_root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
