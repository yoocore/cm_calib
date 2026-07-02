#!/usr/bin/env python3
"""Quick syntax check for all .py files under src/."""
import py_compile, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
src_dir = ROOT / "src"
errors = []

for root, dirs, files in os.walk(src_dir):
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                py_compile.compile(path, doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(str(e))

if errors:
    for e in errors:
        print("[SYNTAX ERROR]", e)
    sys.exit(1)

print("[OK] All source files compile clean")
