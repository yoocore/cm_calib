from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from gui_app.main_window import MainWindow
from portable_runtime import resolve_project_root


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Camera Calibration Console")
    app.setOrganizationName("CMO141")
    window = MainWindow(project_root=resolve_project_root(__file__))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
