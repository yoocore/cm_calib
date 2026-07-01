#!/usr/bin/env python3
"""Test exe without restart logic."""
import sys
import os
from pathlib import Path

# Add parent directory to path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

print("Testing CameraCalibration exe...")
print(f"Executable: {sys.executable}")
print(f"Frozen: {getattr(sys, 'frozen', False)}")
print(f"Args: {sys.argv}")

try:
    # Test basic imports
    from src.entry.launch_gui import main
    print("✓ Successfully imported launch_gui")

    # Test GUI imports
    from PySide6.QtWidgets import QApplication
    print("✓ Successfully imported PySide6")

    # Test main window import
    from src.gui_app.main_window import MainWindow
    print("✓ Successfully imported MainWindow")

    print("\nAll imports successful. Exe should work correctly.")
    print("If exe still restarts, check for:")
    print("1. Process monitoring in parent scripts")
    print("2. Watchdog mechanisms")
    print("3. Auto-update logic")
    print("4. Error handling that triggers restart")

except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
