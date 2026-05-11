from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from gui_app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Camera Calibration Console")
    app.setOrganizationName("CMO141")
    window = MainWindow(project_root=Path(__file__).resolve().parents[3])
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
