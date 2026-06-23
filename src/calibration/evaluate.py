"""EvaluateMixin — evaluate() and optimize() methods for calibration evaluation and optimization."""
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import cv2

from src.calibration.calib_types import BoardScoreDetail, TotalScoreDetail
from src.calibration.sensitivity import build_geometric_sensitivity, get_skip_boards

try:
    import optuna
    _OPTUNA_AVAILABLE = True
except ImportError:
    _OPTUNA_AVAILABLE = False


class EvaluateMixin:

    def evaluate(
        self, tag: str, baseline_metrics: Optional[Dict[str, Dict[str, float]]] = None,
        skip_boards: Optional[Set[str]] = None,
    ) -> Tuple[TotalScoreDetail, Path]:
        if self.real_detections is None:
            self.real_detections = self._detect_reference_boards()

        t0 = time.perf_counter()
        sim_path = self.capture_movie(tag)
        t_capture = time.perf_counter() - t0
        self._last_eval_image = str(sim_path)
        sim_img = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
        if sim_img is None:
            raise RuntimeError(f"Failed reading screenshot: {sim_path}")

        mean_brightness = float(sim_img.mean())
        if mean_brightness < 5.0:
            print(f"[health] Black frame detected (mean={mean_brightness:.1f}), attempting UpdateView recovery...")
            try:
                self._force_update_view()
                sim_path = self.capture_movie(tag + "_blackfix")
                sim_img = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
                if sim_img is not None and float(sim_img.mean()) >= 5.0:
                    print(f"[health] Black frame fixed after UpdateView (mean={float(sim_img.mean()):.1f})")
                elif sim_img is not None:
                    print(f"[health] Black frame persists after UpdateView (mean={float(sim_img.mean()):.1f})")
            except Exception as exc:
                print(f"[health] UpdateView recovery failed: {exc}")

        # Freshness check: detect stale capture (same pixel data as previous)
        current_hash = hash(sim_img.tobytes())
        prev_hash = getattr(self, "_last_capture_hash", None)
        if prev_hash is not None and current_hash == prev_hash:
            stale_count = getattr(self, "_consecutive_stale_count", 0) + 1
            self._consecutive_stale_count = stale_count
            print(f"[health] Stale capture #{stale_count} for '{tag}': image identical to previous. Attempting recovery...")
            if stale_count >= 3:
                raise RuntimeError(f"RENDERING_BROKEN: Too many consecutive stale captures ({stale_count}), rendering permanently frozen")
            try:
                from src.health.rendering_health import try_restart_rendering
                r = try_restart_rendering()
                if r.get("restart_success"):
                    print(f"[health] Rendering restarted (UC growth={r.get('uc_growth')}), re-capturing...")
                    sim_path = self.capture_movie(tag + "_re")
                    sim_img = cv2.imread(str(sim_path), cv2.IMREAD_GRAYSCALE)
                    if sim_img is None:
                        raise RuntimeError(f"Failed reading re-captured screenshot: {sim_path}")
                    re_hash = hash(sim_img.tobytes())
                    if re_hash == current_hash:
                        raise RuntimeError(f"Stale capture persists after rendering restart: {sim_path}")
                    current_hash = re_hash
                else:
                    raise RuntimeError(f"Stale capture and rendering restart failed: {r.get('error', 'unknown')}")
            except RuntimeError:
                raise  # Propagate explicit RuntimeErrors above
            except ImportError:
                raise RuntimeError(f"Stale capture detected (rendering_health not available): {sim_path}")
            except Exception as exc:
                raise RuntimeError(f"Stale capture recovery failed: {exc}")
        else:
            self._consecutive_stale_count = 0
        self._last_capture_hash = current_hash

        sim_prepared = self._prepare_eval_image(sim_img)
        t_prepare = time.perf_counter() - t0 - t_capture

        if not self._sim_templates_generated:
            self._sim_templates_generated = True
            for board in self.boards:
                if board.board_type != "custom_maker" or board.custom_detector != "template_match" or board.roi is None:
                    continue
                rx, ry, rw, rh = board.roi
                if ry + rh > sim_prepared.shape[0] or rx + rw > sim_prepared.shape[1]:
                    continue
                roi_crop = sim_prepared[ry:ry+rh, rx:rx+rw].copy()
                if roi_crop.size == 0:
                    continue
                tpl_info = self.custom_templates.get(board.board_id)
                if tpl_info is not None:
                    new_info = dict(tpl_info)
                    new_info["template"] = roi_crop
                    new_info["match_template"] = None
                    new_info["match_crop"] = None
                    self.custom_templates[board.board_id] = new_info
                else:
                    self.custom_templates[board.board_id] = {
                        "template": roi_crop,
                        "match_template": None,
                        "match_crop": None,
                    }
                self._sim_sourced_board_ids.add(board.board_id)
            if self._sim_sourced_board_ids:
                print(f"[sim-template] Generated simulation-style templates for {len(self._sim_sourced_board_ids)} board(s): {sorted(self._sim_sourced_board_ids)}")

        board_scores: List[BoardScoreDetail] = []
        t_detect_start = time.perf_counter()
        for board in self.boards:
            if skip_boards and board.board_id in skip_boards and baseline_metrics:
                base = baseline_metrics.get(board.board_id, {})
                board_scores.append(BoardScoreDetail(
                    board_id=board.board_id,
                    board_type=getattr(board, 'board_type', ''),
                    success=True,
                    compared=True,
                    reference_visible=True,
                    sim_visible=True,
                    total_score=base.get('total_score', 0.0),
                    rmse=base.get('rmse', 0.0),
                    mean_error=base.get('mean_error', 0.0),
                    max_error=base.get('max_error', 0.0),
                    miss_rate=base.get('miss_rate', 0.0),
                    matched_point_count=int(base.get('matched_point_count', 0)),
                ))
                continue
            real_detection = self.real_detections[board.board_id]
            sim_detection = self._detect_board(sim_prepared, board)
            board_scores.append(self._score_board(board, real_detection, sim_detection, sim_prepared))

        t_detect = time.perf_counter() - t_detect_start
        total_detail = self._aggregate_scores(board_scores, baseline_metrics)
        t_total = time.perf_counter() - t0
        print(f"[timing] capture={t_capture:.2f}s prepare={t_prepare:.2f}s detect+score={t_detect:.2f}s total={t_total:.2f}s boards={len(self.boards)}")
        return total_detail, sim_path

    def optimize(self) -> dict:
        mode = getattr(self, "optimizer_mode", "coordinate_descent")
        if mode == "hybrid" or (mode == "auto" and _OPTUNA_AVAILABLE):
            print(f"Using hybrid (phase1_CD->phase2_Bayesian)" if mode == "auto" else f"Using {mode} optimizer")
            return self._optimize_hybrid()
        if mode == "bayesian" and _OPTUNA_AVAILABLE:
            print("Using bayesian optimizer")
            return self._optimize_bayesian_impl()
        if mode == "auto":
            print("Optuna not available, falling back to coordinate_descent")
        return self._optimize_coordinate_descent_impl()

    def _optimize_hybrid(self) -> dict:
        """P5: CD then Bayesian in a tight search box."""
        phase1_iters = getattr(self, 'hybrid_phase1_iters', 15)
        search_sigma = getattr(self, 'hybrid_search_box_sigma', 3.0)
        total = self.max_iters
        cd_iters = min(phase1_iters, total - 2)
        if cd_iters < 3:
            return self._optimize_coordinate_descent_impl()

        print(f"[hybrid] Phase 1: CD x {cd_iters}")
        cd_result = self._optimize_coordinate_descent_impl(cd_iters)
        cd_score = cd_result.get("final_score", float("inf"))
        cd_values = cd_result.get("final_values", {})

        bayes_iters = total - cd_iters
        print(f"[hybrid] Phase 2: Bayesian x {bayes_iters} around CD best")
        search_range = {}
        for p in self.params:
            step = max(p.step, 1e-6) if p.step else 0.001
            centre = cd_values.get(p.name, p.value)
            half = search_sigma * step
            search_range[p.name] = (
                max(p.min_value, centre - half),
                min(p.max_value, centre + half),
            )
        self._apply_value_map(cd_values)
        import time
        time.sleep(self.settle_sec)

        def objective(trial):
            values = {}
            for param in self.params:
                low, high = search_range[param.name]
                step = max(param.step, 1e-6) if param.step else 0.001
                values[param.name] = trial.suggest_float(param.name, low, high, step=step)
            self._apply_value_map(values)
            time.sleep(self.settle_sec)
            total_detail, _ = self.evaluate("bayes_hybrid", None)
            return float(total_detail.total_score)

        sampler = optuna.samplers.TPESampler(
            multivariate=True,
            n_startup_trials=max(5, min(bayes_iters // 3, 15)),
            seed=42,
        )
        study = optuna.create_study(direction="minimize", sampler=sampler)
        study.enqueue_trial(cd_values)
        study.optimize(objective, n_trials=bayes_iters, n_jobs=1)

        bayes_score = study.best_value
        if bayes_score >= cd_score - self.min_improve:
            # Bayesian didn't improve enough — CD wins
            print(f"[hybrid] CD best retained ({cd_score:.6f} vs Bayesian {bayes_score:.6f})")
            return cd_result

        # Bayesian improved — apply best, capture final
        best_values = {p.name: study.best_params[p.name] for p in self.params}
        self._apply_value_map(best_values)
        time.sleep(self.settle_sec)
        best_total_detail, best_img = self.evaluate("hybrid_final", None)
        print(f"[hybrid] Bayesian improved ({bayes_score:.6f} vs CD {cd_score:.6f})")
        return self._build_result_payload(
            best_score=bayes_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            best_score_image=self._ensure_best_score_image(best_img, best_total_detail, values=best_values),
            best_overlay_image=self._ensure_best_overlay_image(best_img),
            stop_reason="max_iters_reached",
            history=[],
            in_progress=False,
        )
