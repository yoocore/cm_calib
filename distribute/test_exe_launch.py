#!/usr/bin/env python3
"""Test script to diagnose exe restart issues."""
import sys
import os
import traceback
from pathlib import Path

print(f"Python executable: {sys.executable}")
print(f"Frozen: {getattr(sys, 'frozen', False)}")
print(f" argv: {sys.argv}")
print(f"Path: {os.getcwd()}")

try:
    from src.entry.launch_gui import main
    print("Successfully imported launch_gui")
except Exception as e:
    print(f"Import error: {e}")
    traceback.print_exc()

print("Test completed successfully")
