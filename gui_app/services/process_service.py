from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal


class ProcessService(QObject):
    line_received = Signal(str)
    orchestration_event = Signal(dict)
    orchestration_summary = Signal(dict)
    runtime_summary = Signal(dict)
    process_started = Signal()
    process_finished = Signal(int)
    process_failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._process = QProcess(self)
        self._stdout_buffer = ""
        self._process.readyReadStandardOutput.connect(self._handle_stdout)
        self._process.readyReadStandardError.connect(self._handle_stderr)
        self._process.started.connect(self.process_started.emit)
        self._process.finished.connect(self._handle_finished)
        self._process.errorOccurred.connect(self._handle_error)

    @property
    def is_running(self) -> bool:
        return self._process.state() != QProcess.NotRunning

    def start_python(self, script_path: Path, arguments: list[str], working_directory: Path, env: dict | None = None) -> None:
        if self.is_running:
            raise RuntimeError("A process is already running")
        self._stdout_buffer = ""
        self._process.setWorkingDirectory(str(working_directory))
        self._process.setProgram(sys.executable)
        self._process.setArguments([str(script_path), *arguments])
        if env is not None:
            self._process.setProcessEnvironment(os.environ.copy())
            for key, value in env.items():
                self._process.processEnvironment().setValue(key, value)
        else:
            self._process.setProcessEnvironment(os.environ.copy())
        self._process.start()

    def stop(self) -> None:
        if not self.is_running:
            return
        self._process.terminate()
        if not self._process.waitForFinished(3000):
            self._process.kill()

    def _handle_stdout(self) -> None:
        chunk = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._dispatch_text(chunk)

    def _handle_stderr(self) -> None:
        chunk = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        self._dispatch_text(chunk)

    def _dispatch_text(self, chunk: str) -> None:
        self._stdout_buffer += chunk
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            self._handle_line(line.rstrip("\r"))

    def _handle_line(self, line: str) -> None:
        self.line_received.emit(line)
        if line.startswith("ORCHESTRATION_EVENT_JSON:"):
            payload = self._parse_json_suffix(line)
            if payload is not None:
                self.orchestration_event.emit(payload)
        elif line.startswith("ORCHESTRATION_SUMMARY_JSON:"):
            payload = self._parse_json_suffix(line)
            if payload is not None:
                self.orchestration_summary.emit(payload)
        elif line.startswith("CMAPI_CONTROL_SUMMARY_JSON:"):
            payload = self._parse_json_suffix(line)
            if payload is not None:
                self.runtime_summary.emit(payload)


    @staticmethod
    def _parse_json_suffix(line: str) -> dict | None:
        _, _, suffix = line.partition(":")
        suffix = suffix.strip()
        if not suffix:
            return None
        try:
            payload = json.loads(suffix)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _handle_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self._stdout_buffer:
            self._handle_line(self._stdout_buffer.rstrip("\r"))
            self._stdout_buffer = ""
        self.process_finished.emit(exit_code)

    def _handle_error(self, _error: QProcess.ProcessError) -> None:
        self.process_failed.emit(self._process.errorString())
