from __future__ import annotations

import sys
from pathlib import Path

from src.entry.portable_runtime import build_python_command, discover_cmapi_paths, resolve_project_root


def test_discover_cmapi_paths_prefers_matching_python_version(tmp_path):
    cm_install = tmp_path / "win64-14.1"
    cmapi_310 = cm_install / "Python" / "python3.10" / "cmapi"
    cmapi_312 = cm_install / "Python" / "python3.12" / "cmapi"
    cmapi_310.mkdir(parents=True)
    cmapi_312.mkdir(parents=True)

    paths = discover_cmapi_paths(cm_install, version_info=(3, 10))

    assert paths[0] == cmapi_310.parent.resolve()
    assert cmapi_312.parent.resolve() in paths


def test_build_python_command_uses_python_interpreter_when_not_frozen(monkeypatch, tmp_path):
    script_path = tmp_path / "child.py"
    script_path.write_text("print('ok')", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", r"C:\Python310\python.exe")
    monkeypatch.delattr(sys, "frozen", raising=False)

    program, argv = build_python_command(script_path, ["--flag"])

    assert program == r"C:\Python310\python.exe"
    assert argv == [str(script_path.resolve()), "--flag"]


def test_build_python_command_dispatches_through_exe_when_frozen(monkeypatch, tmp_path):
    script_path = tmp_path / "child.py"
    script_path.write_text("print('ok')", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", r"C:\Portable\CameraCalibrationGUI.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    program, argv = build_python_command(script_path, ["--flag"])

    assert program == r"C:\Portable\CameraCalibrationGUI.exe"
    assert argv == ["--camcal-dispatch", str(script_path.resolve()), "--flag"]


def test_resolve_project_root_prefers_exe_dir_for_frozen_portable_layout(monkeypatch, tmp_path):
    portable_root = tmp_path / "portable"
    (portable_root / "Data" / "Script" / "CameraCalibration").mkdir(parents=True)
    fake_exe = portable_root / "CameraCalibrationGUI.exe"
    fake_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert resolve_project_root() == portable_root.resolve()
