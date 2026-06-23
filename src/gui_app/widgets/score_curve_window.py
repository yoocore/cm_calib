from __future__ import annotations

import json
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QVBoxLayout, QWidget


def _compute_board_sig(boards: list) -> frozenset | None:
    if not isinstance(boards, list) or not boards:
        return None
    entries = []
    for board in boards:
        if not isinstance(board, dict):
            return None
        bid = board.get("board_id")
        btype = board.get("board_type")
        if not bid or not btype:
            return None
        entries.append((str(bid), str(btype)))
    return frozenset(entries)


def _board_sig_from_json(path: Path) -> frozenset | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return _compute_board_sig(payload.get("boards"))


class ScoreCurveWindow(QWidget):

    def __init__(
        self,
        camera_name: str,
        result_json_path: str,
        *,
        mode: str = "live",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._camera_name = camera_name
        self._result_json_path = Path(result_json_path)
        self._mode = mode
        self._timer: QTimer | None = None

        title = f"Score Live - {camera_name}" if mode == "live" else f"Score Plot - {camera_name}"
        self.setWindowTitle(title)
        self.resize(700, 450)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setLabel("left", "total_score")
        self._plot_widget.setLabel("bottom", "iteration")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self._plot_widget.addLegend()
        layout.addWidget(self._plot_widget)

        self._curve = self._plot_widget.plot(
            pen=pg.mkPen(color=(31, 111, 235), width=2),
            name=camera_name,
        )

        if mode == "live":
            self._refresh()
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._refresh)
            self._timer.start(2000)
        elif mode == "plot":
            self._refresh_static()

    def _refresh(self) -> None:
        data = self._read_curve(self._result_json_path)
        if data:
            x, y = zip(*data)
            self._curve.setData(x=x, y=y)

    def _refresh_static(self) -> None:
        data = self._read_curve(self._result_json_path)
        if not data:
            return
        x, y = zip(*data)
        self._curve.setData(x=x, y=y)

        current_sig = _board_sig_from_json(self._result_json_path)
        if current_sig is None:
            return

        summary_path = self._summary_path()
        if not summary_path or not summary_path.exists():
            return

        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        runs = [r for r in (summary.get("runs") or []) if isinstance(r, dict)]
        same_config_scores = []
        for run in runs:
            hist_path_raw = run.get("result_json")
            if not hist_path_raw:
                continue
            hist_path = Path(hist_path_raw)
            if not hist_path.exists():
                continue
            hist_sig = _board_sig_from_json(hist_path)
            if hist_sig == current_sig:
                final = run.get("final_score")
                if final is not None:
                    same_config_scores.append(float(final))

        if same_config_scores:
            self._plot_widget.plot(
                list(range(len(same_config_scores))),
                same_config_scores,
                pen=None,
                symbol="o",
                symbolSize=8,
                symbolBrush=(31, 111, 235, 120),
                name=f"Historical ({len(same_config_scores)} runs)",
            )

    def _read_curve(self, path: Path) -> list[tuple[int, float]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return []
        history = payload.get("history")
        if not isinstance(history, list):
            return []
        points = []
        for entry in history:
            if not isinstance(entry, dict):
                continue
            it = entry.get("iter")
            ts = entry.get("total_score")
            if it is None or ts is None:
                continue
            points.append((int(it), float(ts)))
        points.sort(key=lambda p: p[0])
        return points

    def _summary_path(self) -> Path | None:
        try:
            camera_dir = self._result_json_path.parent.parent
        except Exception:
            return None
        return camera_dir / "camera_summary.json"

    def closeEvent(self, event) -> None:
        if self._timer is not None:
            self._timer.stop()
        super().closeEvent(event)
