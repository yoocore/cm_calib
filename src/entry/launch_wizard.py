from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    print(f"[wizard] Python: {sys.executable}")
    print(f"[wizard] CWD: {Path.cwd()}")
    print(f"[wizard] Script dir: {Path(__file__).resolve().parent}")
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    from PySide6.QtWidgets import QApplication
    print("[wizard] PySide6 loaded")

    app = QApplication(sys.argv)
    app.setApplicationName("Board Calibration Wizard")
    print("[wizard] QApplication created")

    from src.gui_app.widgets.bootstrap_wizard import BootstrapWizardDialog
    print("[wizard] Wizard imported")

    dialog = BootstrapWizardDialog()
    dialog.show()
    print("[wizard] Dialog shown - check your taskbar")
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        input("Press Enter to exit...")
