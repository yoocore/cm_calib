from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, Sequence


CM_INSTALL_SEARCH_ROOTS = (
    "D:/IPG/carmaker",
    "C:/IPG/carmaker",
    "D:/IPG",
    "C:/IPG",
    "D:/CarMaker",
    "C:/CarMaker",
    "D:/Program Files/IPG/carmaker",
    "C:/Program Files/IPG/carmaker",
    "D:/Program Files/CarMaker",
    "C:/Program Files/CarMaker",
)
_LEGACY_CMAPI_SUBDIRS = (
    "Python/Lib/site-packages",
    "Python/Lib",
    "pylib",
    "Lib/site-packages",
    "Lib",
)
_CMAPI_MARKERS = ("cmapi", "cmapi.py", "cmapi.pyd")


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve()
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _has_cmapi_marker(path: Path) -> bool:
    if not path.is_dir():
        return False
    for marker in _CMAPI_MARKERS:
        if (path / marker).exists():
            return True
    if any(path.glob("cmapi-*.whl")):
        return True
    return any(path.glob("apoc*.pyd")) or any(path.glob("infofiles*.pyd"))


def _current_python_dir_names(version_info: tuple[int, int] | None = None) -> list[str]:
    major, minor = version_info or (sys.version_info.major, sys.version_info.minor)
    return [
        f"python{major}.{minor}",
        f"python{major}{minor}",
    ]


def discover_carmaker_installs() -> list[Path]:
    installs: list[Path] = []
    for root in CM_INSTALL_SEARCH_ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for entry in sorted(root_path.iterdir(), reverse=True):
            if entry.is_dir() and entry.name.startswith("win64-"):
                installs.append(entry.resolve())
    return _unique_paths(installs)


def discover_cmapi_paths(
    cm_install: Path | None = None,
    *,
    version_info: tuple[int, int] | None = None,
) -> list[Path]:
    installs = [cm_install.resolve()] if cm_install is not None else discover_carmaker_installs()
    preferred: list[Path] = []
    fallback: list[Path] = []
    preferred_names = {name.casefold() for name in _current_python_dir_names(version_info)}

    for install in installs:
        python_root = install / "Python"
        if python_root.is_dir():
            if _has_cmapi_marker(python_root):
                preferred.append(python_root)
            for child in sorted(python_root.iterdir()):
                if not child.is_dir():
                    continue
                child_name = child.name.casefold()
                if not child_name.startswith("python"):
                    continue
                if not _has_cmapi_marker(child):
                    continue
                if child_name in preferred_names:
                    preferred.append(child)
                else:
                    fallback.append(child)

        for rel_path in _LEGACY_CMAPI_SUBDIRS:
            candidate = install / rel_path
            if _has_cmapi_marker(candidate):
                fallback.append(candidate)

    ordered = preferred or (preferred + fallback)
    if preferred:
        ordered = preferred + fallback
    else:
        ordered = fallback
    return _unique_paths(ordered)


def resolve_default_cm_install() -> Path | None:
    """Return the most recent CarMaker installation discovered on this system.

    Used as a fallback when no explicit cm-install is provided.
    """
    installs = discover_carmaker_installs()
    return installs[0] if installs else None


def build_cmapi_pythonpath(
    cm_install: Path | None,
    *,
    existing_pythonpath: str = "",
    version_info: tuple[int, int] | None = None,
) -> tuple[str, list[Path]]:
    paths = discover_cmapi_paths(cm_install, version_info=version_info)
    if not paths:
        return existing_pythonpath, []
    prefix = ";".join(str(path) for path in paths)
    combined = f"{prefix};{existing_pythonpath}" if existing_pythonpath else prefix
    return combined, paths


def apply_cmapi_to_current_process(
    cm_install: Path | None = None,
    *,
    version_info: tuple[int, int] | None = None,
) -> list[Path]:
    if cm_install is None:
        cm_install = resolve_default_cm_install()
    if cm_install is None:
        return []
    paths = discover_cmapi_paths(cm_install, version_info=version_info)
    for path in reversed(paths):
        as_text = str(path)
        if as_text not in sys.path:
            sys.path.insert(0, as_text)
    return paths


def resolve_tool_root() -> Path:
    return Path(__file__).resolve().parents[2]

def ensure_calibration_root_on_sys_path(project_root: Path | None = None) -> Path:
    calibration_root = resolve_tool_root()
    calibration_root_text = str(calibration_root)
    if calibration_root_text not in sys.path:
        sys.path.insert(0, calibration_root_text)
    return calibration_root


def build_python_command(
    script_path: Path,
    arguments: Sequence[str],
    *,
    python_executable: Path | None = None,
) -> tuple[str, list[str]]:
    resolved_script_path = script_path.resolve()
    if python_executable is not None:
        return str(python_executable.resolve()), [str(resolved_script_path), *arguments]
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve()), ["--camcal-dispatch", str(resolved_script_path), *arguments]
    return str(Path(sys.executable).resolve()), [str(resolved_script_path), *arguments]


def build_python_subprocess_command(
    script_path: Path,
    arguments: Sequence[str],
    *,
    python_executable: Path | None = None,
) -> list[str]:
    program, argv = build_python_command(
        script_path,
        arguments,
        python_executable=python_executable,
    )
    return [program, *argv]
