from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    from gui_app.widgets.bootstrap_wizard import BootstrapWizardDialog

    app = QApplication(sys.argv)
    app.setApplicationName("Board Calibration Wizard")

    dialog = BootstrapWizardDialog()
    dialog.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
