from __future__ import annotations

import json
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
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
        paths = self._discover_campaign_result_jsons(self._result_json_path)
        data = self._read_campaign_curve(paths)
        if data:
            x, y = zip(*data)
            self._curve.setData(x=x, y=y)
        markers = self._read_stop_markers(paths)
        if markers and data:
            self._draw_stop_markers(markers, max(y))

    def _refresh_static(self) -> None:
        paths = self._discover_campaign_result_jsons(self._result_json_path)
        data = self._read_campaign_curve(paths)
        if not data:
            return
        x, y = zip(*data)
        self._curve.setData(x=x, y=y)

        markers = self._read_stop_markers(paths)
        if markers:
            self._draw_stop_markers(markers, max(y))

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
        
        for run in runs:
            hist_path_raw = run.get("result_json")
            if not hist_path_raw:
                continue
            hist_path = Path(hist_path_raw)
            if not hist_path.exists():
                continue
            hist_sig = _board_sig_from_json(hist_path)
            if hist_sig != current_sig:
                continue
            hist_paths = self._discover_campaign_result_jsons(hist_path)
            hist_data = self._read_campaign_curve(hist_paths)
            if not hist_data:
                continue
            hx, hy = zip(*hist_data)
            self._plot_widget.plot(
                hx, hy,
                pen=pg.mkPen(color=(31, 111, 235, 50), width=1),
                name=None,
            )


    def _discover_campaign_result_jsons(self, seed_path: Path) -> list[Path]:
        seed = seed_path.resolve()
        for depth in range(3):
            candidate = seed
            for _ in range(depth):
                candidate = candidate.parent
            explore_dir = candidate / "explore"
            refine_dir = candidate / "refine"
            if explore_dir.is_dir() or refine_dir.is_dir():
                results: list[Path] = []
                if explore_dir.is_dir():
                    for run_dir in sorted(explore_dir.iterdir()):
                        if run_dir.is_dir() and run_dir.name.startswith("start_"):
                            self._add_result_jsons(run_dir, results)
                if refine_dir.is_dir():
                    self._add_result_jsons(refine_dir, results)
                if results:
                    return results
        return [seed_path]

    def _add_result_jsons(self, directory: Path, results: list[Path]) -> None:
        main = directory / "result.json"
        if main.exists():
            results.append(main)
        for archive in sorted(directory.glob("result_*.json")):
            if archive != main:
                results.append(archive)

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

    def _read_campaign_curve(self, paths: list[Path]) -> list[tuple[int, float]]:
        all_points: list[tuple[int, float]] = []
        cumulative_iter = 0
        for path in paths:
            points = self._read_curve(path)
            if not points:
                continue
            for it, score in points:
                all_points.append((cumulative_iter + it, score))
            cumulative_iter += max(p[0] for p in points) + 1 if points else 0
        return all_points

    def _read_stop_markers(self, paths: list[Path]) -> list[tuple[int, str]]:
        markers: list[tuple[int, str]] = []
        cumulative_iter = 0
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            stop_reason = payload.get("stop_reason", "")
            if stop_reason and stop_reason not in ("running", ""):
                history = payload.get("history")
                if isinstance(history, list) and history:
                    last_iter = max(int(e.get("iter", 0)) for e in history if isinstance(e, dict))
                    markers.append((cumulative_iter + last_iter, stop_reason))
            history = payload.get("history")
            if isinstance(history, list) and history:
                max_iter = max(int(e.get("iter", 0)) for e in history if isinstance(e, dict))
                cumulative_iter += max_iter + 1
        return markers

    def _draw_stop_markers(self, markers: list[tuple[int, str]], max_y: float) -> None:
        stop_colors = {
            "target_score": (76, 175, 80),        # green
            "direction_accepted": (33, 150, 243),  # blue
            "all_steps_minimum": (244, 67, 54),    # red
            "bayesian_converged": (244, 67, 54),   # red
            "max_iters_reached": (158, 158, 158),  # gray
        }
        stop_labels = {
            "target_score": "target",
            "direction_accepted": "accepted",
            "all_steps_minimum": "converged",
            "bayesian_converged": "converged",
            "max_iters_reached": "max_iters",
        }
        for x, reason in markers:
            color = stop_colors.get(reason, (158, 158, 158))
            label = stop_labels.get(reason, reason)
            self._plot_widget.addLine(
                x=x,
                pen=pg.mkPen(color=color, width=1.5, style=Qt.PenStyle.DashLine),
                label=f"{label}",
                labelOpts={"color": color, "position": 0.95},
            )

    def _summary_path(self) -> Path | None:
        try:
            current = self._result_json_path.resolve()
        except Exception:
            return None
        for _ in range(10):
            for name in ("camera_history_summary.json", "camera_summary.json"):
                candidate = current / name
                if candidate.exists():
                    return candidate
            parent = current.parent
            if not parent or parent == current:
                break
            current = parent
        return None

    def closeEvent(self, event) -> None:
        if self._timer is not None:
            self._timer.stop()
        super().closeEvent(event)
