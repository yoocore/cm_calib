from __future__ import annotations

import sys

from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from src.gui_app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Camera Calibration Console")
    app.setOrganizationName("CMO141")

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
