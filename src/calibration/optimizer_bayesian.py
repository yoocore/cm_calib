try:
    import optuna
    _OPTUNA_AVAILABLE = True
except ImportError:
    _OPTUNA_AVAILABLE = False


class BayesianOptimizerMixin:
    """Bayesian (Optuna) optimization methods for CameraCalibrator."""

    def _optimize_bayesian(self) -> dict:
        if not _OPTUNA_AVAILABLE:
            print("WARNING: optuna not installed, falling back to coordinate_descent")
            return self._optimize_coordinate_descent_impl()
        return self._optimize_bayesian_impl()

    def _optimize_bayesian_impl(self) -> dict:
        cfg = self.cfg
        self._ensure_live_log()
        self._historical_best_snapshot = None
        if self.real_detections is None:
            self.real_detections = self._detect_reference_boards()

        self._print_run_summary()
        self._preflight_capture_aspect_ratio()
        self.preflight_script_control()
        try:
            self._apply_initial_value_map_with_retry(
                self._snapshot_values(),
                "Failed initial Script Control apply",
            )
        except RuntimeError as exc:
            print(f"Initial Script Control apply skipped: {exc}")

        best_total_detail, best_img = self.evaluate("initial", baseline_metrics=None)
        self._raise_if_initial_board_failures(best_total_detail)
        best_score = best_total_detail.total_score
        best_baseline = self._as_baseline_metrics(best_total_detail)
        best_values = {p.name: p.value for p in self.params}
        stop_reason = "max_iters_reached"
        initial_score_image = self._build_score_image_for_snapshot(
            best_img,
            best_total_detail,
            best_values,
            output_path=best_img.with_name("initial_score.png"),
        )

        history = [
            {
                "iter": 0,
                "total_score": best_score,
                "compared_board_count": best_total_detail.compared_board_count,
                "degrade_penalty": best_total_detail.degrade_penalty,
                "has_critical_degrade": best_total_detail.has_critical_degrade,
                "degraded_boards": best_total_detail.degraded_boards,
                "board_scores": [
                    {
                        "board_id": s.board_id,
                        "board_type": s.board_type,
                        "compared": s.compared,
                        "reference_visible": s.reference_visible,
                        "sim_visible": s.sim_visible,
                        "score": s.total_score,
                        "rmse": s.rmse,
                        "mean_error": s.mean_error,
                        "max_error": s.max_error,
                        "miss_rate": s.miss_rate,
                        "matched_point_count": s.matched_point_count,
                        "failed_reason": s.failed_reason,
                    }
                    for s in best_total_detail.board_scores
                ],
                "accepted": True,
                "image": str(best_img),
                "score_image": str(initial_score_image) if initial_score_image else None,
                "values": best_values.copy(),
            }
        ]

        print(
            "iter=0 "
            f"total_score={best_score:.6f} "
            f"compared_boards={best_total_detail.compared_board_count} "
            f"degrade_penalty={best_total_detail.degrade_penalty:.6f} "
            f"{self._top_board_summary(best_total_detail)}"
        )
        self._write_progress_result(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason="running",
            history=history,
            in_progress=True,
        )

        def objective(trial: optuna.Trial) -> float:
            trial_values = {}
            self._total_trial_count += 1
            for p in self.params:
                trial_values[p.name] = trial.suggest_float(
                    p.name,
                    float(p.min_value),
                    float(p.max_value),
                )

            self._apply_value_map_or_recover(
                trial_values,
                f"Failed to apply trial values in Bayesian iteration {trial.number}",
            )

            tag = f"bayesian_trial_{trial.number:04d}"
            try:
                total_detail, img_path = self.evaluate(
                    tag,
                    baseline_metrics=best_baseline,
                )
                score = total_detail.total_score

                accepted, accepted_reason = self._acceptance_decision(
                    baseline_score=best_score,
                    baseline_detail=best_total_detail,
                    candidate_score=score,
                    candidate_detail=total_detail,
                )

                history.append(
                    self._make_history_entry(
                        trial.number + 1,
                        total_detail,
                        img_path,
                        accepted,
                        meta={
                            "phase": "bayesian",
                            "values": trial_values.copy(),
                            "accepted_reason": accepted_reason,
                        },
                    )
                )
                history_trimmed = self._trim_history(history)

                self._append_trial_log(
                    iteration=trial.number + 1,
                    score=score,
                    accepted=accepted,
                    phase="bayesian",
                    accepted_reason=accepted_reason,
                )

                print(
                    f"iter={trial.number + 1} phase=bayesian "
                    f"total_score={score:.6f} compared={total_detail.compared_board_count} "
                    f"degrade={total_detail.degrade_penalty:.6f} "
                    f"critical_degrade={total_detail.has_critical_degrade} "
                    f"accepted={accepted} accepted_reason={accepted_reason} "
                    f"{self._top_board_summary(total_detail)}"
                )

                if trial.number % self.progress_flush_every == 0:
                    self._write_progress_result(
                        best_score=best_score,
                        best_values=best_values,
                        best_total_detail=best_total_detail,
                        best_img=best_img,
                        stop_reason="running",
                        history=history_trimmed,
                        in_progress=True,
                    )

                return score
            except RuntimeError as exc:
                print(f"iter={trial.number + 1} phase=bayesian runtime_error={exc}")
                self._apply_value_map_or_recover(
                    best_values,
                    f"Failed to restore best values after Bayesian runtime error: {exc}",
                )
                history.append(
                    self._make_history_entry(
                        trial.number + 1,
                        best_total_detail,
                        best_img,
                        False,
                        failed_reason=str(exc),
                        meta={"phase": "bayesian_runtime_error"},
                    )
                )
                self._total_trial_count += 1
                self._append_trial_log(
                    iteration=trial.number + 1,
                    score=float("inf"),
                    accepted=False,
                    phase="bayesian_runtime_error",
                    failed_reason=str(exc),
                )
                return float("inf")

        sampler = optuna.samplers.TPESampler(
            seed=int(cfg.get("bayesian_seed", 42)),
            n_startup_trials=min(5, max(2, self.max_iters // 10)),
        )
        study = optuna.create_study(
            direction="minimize",
            sampler=sampler,
            study_name=f"calibration_{self.camera_name}",
        )

        study.enqueue_trial(best_values)

        n_trials = self.max_iters
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

        if study.best_trial is not None:
            best_params = study.best_params
            best_score = study.best_value
            self._apply_value_map_or_recover(
                best_params,
                "Failed to apply best Bayesian parameters",
            )
            best_total_detail, best_img = self.evaluate(
                "bayesian_best",
                baseline_metrics=None,
            )
            best_values = best_params.copy()
            stop_reason = "bayesian_converged"

        result = self._build_result_payload(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            best_score_image=self._ensure_best_score_image(
                best_img,
                best_total_detail,
                values=best_values,
            ),
            best_overlay_image=self._ensure_best_overlay_image(best_img),
            stop_reason=stop_reason,
            history=self._trim_history(history),
            in_progress=False,
        )
        self._print_acceptance_summary(best_total_detail)
        self._print_calibration_summary(result["summary"])
        self._write_progress_result(
            best_score=best_score,
            best_values=best_values,
            best_total_detail=best_total_detail,
            best_img=best_img,
            stop_reason=stop_reason,
            history=self._trim_history(history),
            in_progress=False,
        )
        return result
