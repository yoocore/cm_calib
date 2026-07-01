import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.calibration.orchestration import (
    _acquire_runtime_session_lock,
    _build_isolated_output_dir,
    _camera_name_from_config_path,
    _configure_live_log,
    _load_json_if_exists,
    _marker_path_for_output_dir,
    _print_camera_history_summary,
    _print_camera_history_summary_compact,
    _probe_runtime_vehicle_context,
    _read_latest_result_path,
    _read_vehicle_initial_values_mandatory,
    _resolve_config_output_dir,
    _run_explore_then_refine_rounds,
    _run_plain_optimize_rounds,
    _verify_vehicle_writeback,
    _write_best_values_to_vehicle_config,
    _write_camera_history_summary,
    _write_camera_history_summary_compact,
    _write_run_marker,
)
from src.calibration.config import (
    _default_bootstrap_template_path,
    bootstrap_config_from_annotation,
)
from src.health.precheck_cli import run_precheck


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IPGMovie camera calibration multi-board matching loop"
    )
    parser.add_argument(
        "--precheck",
        action="store_true",
        help="Run camera precheck (check raw images, configs) and exit",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="CarMaker project root (for precheck mode)",
    )
    parser.add_argument(
        "--camera",
        action="append",
        dest="cameras",
        default=[],
        help="Camera sensor name to precheck. Repeat for multiple cameras.",
    )
    parser.add_argument(
        "--config",
        required=False,
        help="Path to runtime JSON config, for example configs/camera.rear_tv.json; required except in bootstrap mode",
    )
    parser.add_argument(
        "--capture-initials",
        action="store_true",
        help="Read current Script Control values and print initial values",
    )
    parser.add_argument(
        "--propose-boards",
        action="store_true",
        help="Auto-detect candidate board instances from real_image and write a proposed config",
    )
    parser.add_argument(
        "--proposal-output",
        default=None,
        help="Optional path for proposed config output",
    )
    parser.add_argument(
        "--proposal-preview",
        default=None,
        help="Optional path for proposal preview image output",
    )
    parser.add_argument(
        "--bootstrap-config-from-annotation",
        action="store_true",
        help="Generate a new camera config from a real image plus a manually annotated red-box image",
    )
    parser.add_argument(
        "--bootstrap-real-image",
        default=None,
        help="Real camera image used as the new config real_image",
    )
    parser.add_argument(
        "--bootstrap-template-config",
        default=None,
        help="Path to standalone bootstrap template input; defaults to configs/bootstrap.template.json next to the script",
    )
    parser.add_argument(
        "--bootstrap-annotated-image",
        default=None,
        help="Manually annotated image with red rectangles around boards",
    )
    parser.add_argument(
        "--bootstrap-output",
        default=None,
        help="Optional output path for the generated config",
    )
    parser.add_argument(
        "--bootstrap-preview",
        default=None,
        help="Optional output path for the generated preview image",
    )
    parser.add_argument(
        "--bootstrap-camera-name",
        default=None,
        help="Optional camera name override for generated config/output naming",
    )
    parser.add_argument(
        "--bootstrap-skip-current-params",
        action="store_true",
        help="Skip reading current window parameters through Script Control during config bootstrap",
    )
    parser.add_argument(
        "--annotate-image",
        default=None,
        help="Annotate an existing simulation image using the current config",
    )
    parser.add_argument(
        "--annotate-output",
        default=None,
        help="Optional output path for --annotate-image",
    )
    parser.add_argument(
        "--explore-start-count",
        type=int,
        default=4,
        help="Number of perturbed explore runs per camera"
    )
    parser.add_argument(
        "--explore-jitter-steps",
        type=str,
        default="auto",
        help="Jitter: auto (adaptive) or float value"
    )
    parser.add_argument(
        "--explore-start-seed",
        type=int,
        default=20260429,
        help="Random seed for explore initial value generation"
    )
    parser.add_argument(
        "--explore-then-refine",
        action="store_true",
        dest="explore_then_refine",
        default=True,
        help="Run a short exploration first, then launch one refinement run from the best explored start (default)"
    )
    parser.add_argument(
        "--no-explore-then-refine",
        action="store_false",
        dest="explore_then_refine",
        help="Disable explore-then-refine and fall back to single optimize"
    )
    parser.add_argument(
        "--refine-iters",
        type=int,
        default=None,
        help="Optional max_iters override for the refinement phase of --explore-then-refine",
    )
    parser.add_argument(
        "--explore-iters",
        type=int,
        default=None,
        help="Optional max_iters override for the exploration phase of --explore-then-refine",
    )
    parser.add_argument(
        "--resume-from-result",
        action="store_true",
        help="Optional legacy mode: resume parameter values from the last result before optimize",
    )
    parser.add_argument(
        "--campaign-rounds",
        type=int,
        default=1,
        help="Repeat multi-start or explore-then-refine for N outer rounds, carrying previous best values and learned param order into the next round",
    )
    parser.add_argument(
        "--verbose-dde-diag",
        action="store_true",
        help="Print per-attempt DDE success diagnostics; retry/failed logs remain enabled by default",
    )
    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="Print one machine-readable JSON summary line on successful completion",
    )
    parser.add_argument(
        "--print-progress-json",
        action="store_true",
        help="Print machine-readable JSON progress lines whenever result.json is refreshed",
    )
    return parser.parse_args()


def _build_cli_summary_payload(
    *,
    camera_name: str,
    config_path: Path,
    mode: str,
    result_json_path: Optional[Path] = None,
    result_payload: Optional[dict] = None,
    summary_json_path: Optional[Path] = None,
    rounds_output_dir: Optional[Path] = None,
) -> dict:
    payload = result_payload or {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    best_score = payload.get("best_score")
    if best_score is None:
        best_score = summary.get("final_score")

    return {
        "camera": camera_name,
        "config_path": str(config_path),
        "mode": mode,
        "result_json": str(result_json_path) if result_json_path else None,
        "summary_json": str(summary_json_path) if summary_json_path else None,
        "rounds_output_dir": str(rounds_output_dir) if rounds_output_dir else None,
        "output_dir": payload.get("output_dir"),
        "in_progress": bool(payload.get("in_progress", False)),
        "best_score": best_score,
        "best_image": payload.get("best_image"),
        "best_score_image": payload.get("best_score_image"),
        "best_overlay_image": payload.get("best_overlay_image"),
        "current_iter_index": summary.get("current_iter_index"),
        "current_iter_score": summary.get("current_iter_score"),
        "final_score": summary.get("final_score"),
        "passed": summary.get("passed"),
        "stop_reason": payload.get("stop_reason") or summary.get("stop_reason"),
        "live_log": payload.get("live_log"),
        "run_session_id": payload.get("run_session_id"),
    }


def _emit_cli_summary_json(payload: dict) -> None:
    print("CALIBRATION_SUMMARY_JSON:", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _emit_cli_progress_json(payload: dict) -> None:
    print("CALIBRATION_PROGRESS_JSON:", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _auto_detect_cameras(project_root: Path) -> list[str]:
    config_dir = project_root / "Data" / "Script" / "CameraCalibration" / "configs"
    if not config_dir.is_dir():
        return []
    import re as _cam_re
    pattern = _cam_re.compile(r"^camera\.(.+)\.json$")
    names: list[str] = []
    for f in config_dir.iterdir():
        m = pattern.match(f.name)
        if m and not f.name.endswith(".bak.json"):
            names.append(m.group(1))
    return sorted(names)


def main() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True, write_through=True)
        sys.stderr.reconfigure(line_buffering=True, write_through=True)
    except Exception:
        pass

    args = parse_args()

    if args.refine_iters is not None and args.refine_iters < 0:
        raise ValueError("--refine-iters must be >= 0")
    if args.campaign_rounds <= 0:
        raise ValueError("--campaign-rounds must be > 0")
    root = args.project_root.resolve() if args.project_root else Path.cwd()

    if args.precheck:
        cameras = args.cameras if args.cameras else _auto_detect_cameras(root)
        results = run_precheck(root, cameras)
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    if args.bootstrap_config_from_annotation:
        if not args.bootstrap_real_image or not args.bootstrap_annotated_image:
            raise ValueError(
                "--bootstrap-config-from-annotation requires --bootstrap-real-image and --bootstrap-annotated-image"
            )
        if args.explore_then_refine or args.resume_from_result:
            raise ValueError(
                "bootstrap-config-from-annotation cannot be combined with optimization campaign options"
            )
        if args.propose_boards or args.annotate_image or args.capture_initials:
            raise ValueError(
                "bootstrap-config-from-annotation cannot be combined with capture/propose/annotate commands"
            )
        template_config_path = (
            Path(args.bootstrap_template_config).resolve()
            if args.bootstrap_template_config
            else _default_bootstrap_template_path()
        )
        bootstrap_config_from_annotation(
            template_config_path=template_config_path,
            real_image_path=Path(args.bootstrap_real_image),
            annotated_image_path=Path(args.bootstrap_annotated_image),
            output_path=Path(args.bootstrap_output) if args.bootstrap_output else None,
            preview_path=Path(args.bootstrap_preview) if args.bootstrap_preview else None,
            camera_name=args.bootstrap_camera_name,
            capture_current_params=not args.bootstrap_skip_current_params,
        )
        return

    if not args.config:
        raise ValueError("--config is required unless --bootstrap-config-from-annotation is used")

    config_path = Path(args.config).resolve()
    camera_name = _camera_name_from_config_path(config_path)
    with open(config_path, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    if args.verbose_dde_diag:
        cfg["verbose_dde_diag"] = True

    base_output_dir = _resolve_config_output_dir(cfg, config_path, project_root=root)
    cfg["output_dir"] = str(base_output_dir)
    should_optimize = not any(
        [
            args.propose_boards,
            bool(args.annotate_image),
            args.capture_initials,
        ]
    )
    requires_runtime_session = bool(args.capture_initials) or should_optimize

    if requires_runtime_session and should_optimize:
        print(f"Config initial values BEFORE vehicle DDE read for {camera_name}:")
        for name, param in sorted(cfg.get("parameters", {}).items()):
            if "initial" in param:
                print(f"  {name}: {param['initial']}")
            else:
                print(f"  {name}: (no initial)")
        _vehicle_initial_values = _read_vehicle_initial_values_mandatory(camera_name, project_root=root)
        print(f"Vehicle DDE read returned {len(_vehicle_initial_values)} values:")
        for name, value in sorted(_vehicle_initial_values.items()):
            print(f"  {name}: {value}")
        for name, value in _vehicle_initial_values.items():
            if name in cfg.get("parameters", {}):
                cfg["parameters"][name]["initial"] = value
            else:
                print(f"  WARNING: {name} from vehicle file not in config parameters")
        runtime_context = _probe_runtime_vehicle_context(project_root=root)
        if runtime_context and runtime_context.get("vehicle_path"):
            cfg.setdefault("vehicle_writeback", {}).setdefault("vehicle", str(runtime_context["vehicle_path"]))
            print(f"[writeback] Vehicle path cached from probe: {runtime_context['vehicle_path']}")
        else:
            print(
                "WARNING: Vehicle writeback will NOT be available — "
                f"vehicle path probe returned {runtime_context}. "
                "Calibration results will not be saved to the vehicle file. "
                "Next calibration will start from original vehicle defaults."
            )
        print(f"Config initial values AFTER vehicle DDE read for {camera_name}:")
        for name, param in sorted(cfg.get("parameters", {}).items()):
            if "initial" in param:
                print(f"  {name}: {param['initial']}")
            else:
                print(f"  {name}: (no initial)")

    if requires_runtime_session:
        _acquire_runtime_session_lock(base_output_dir, config_path)

    if should_optimize and args.explore_then_refine:
        if args.resume_from_result:
            print("Explore-then-refine mode ignores --resume-from-result and always starts from config initial values.")
        campaign_start_count = args.explore_start_count
        if args.explore_iters is not None:
            campaign_explore_iters = int(args.explore_iters)
        else:
            campaign_explore_iters = int(cfg.get("max_iters", 100))
        rounds_payload = _run_explore_then_refine_rounds(
            config_path=config_path,
            cfg=cfg,
            base_output_dir=base_output_dir,
            camera_name=camera_name,
            round_count=int(args.campaign_rounds),
            start_count=campaign_start_count,
            jitter_steps=args.explore_jitter_steps,
            seed=int(args.explore_start_seed),
            explore_max_iters=int(campaign_explore_iters),
            refine_max_iters=args.refine_iters,
            project_root=root,
        )
        best_round = rounds_payload["best_round"] or {}
        best_run = best_round.get("best_run") or {}
        print("Rounds summary JSON:", rounds_payload["summary_json"])
        print("Rounds output dir:", rounds_payload["rounds_output_dir"])
        print("Completed rounds:", rounds_payload["round_count_completed"])
        print("Best round index:", best_round.get("round_index"))
        print("Campaign best stage:", best_run["stage"])
        print("Campaign best score:", best_run["best_score"])
        print("Campaign best image:", best_run["best_image"])
        print("Campaign best result JSON:", best_run["result_json"])
        camera_history_summary_path, camera_history_summary = _write_camera_history_summary(camera_name, project_root=root)
        camera_history_summary_compact_path = _write_camera_history_summary_compact(
            camera_name,
            camera_history_summary,
            project_root=root,
        )
        _print_camera_history_summary(camera_history_summary, camera_history_summary_path)
        _print_camera_history_summary_compact(camera_history_summary_compact_path)
        if args.print_summary_json:
            best_result_json_path = Path(best_run["result_json"]).resolve()
            _emit_cli_summary_json(
                _build_cli_summary_payload(
                    camera_name=camera_name,
                    config_path=config_path,
                    mode="explore_then_refine_rounds",
                    result_json_path=best_result_json_path,
                    result_payload=_load_json_if_exists(best_result_json_path) or {},
                    summary_json_path=Path(rounds_payload["summary_json"]).resolve(),
                    rounds_output_dir=Path(rounds_payload["rounds_output_dir"]).resolve(),
                )
            )

        if best_run:
            wb_result = _write_best_values_to_vehicle_config(
                config_path, cfg, camera_name,
                float(best_run.get("best_score", 999)),
                best_run.get("best_values", {}),
                project_root=root,
            )
            if wb_result is None:
                print(
                    "WARNING: Vehicle writeback failed — results will NOT persist. "
                    "Next calibration will start from original vehicle defaults."
                )
            else:
                _verify_vehicle_writeback(
                    config_path, cfg, camera_name, wb_result,
                    best_run.get("best_values", {}),
                    project_root=root,
                )
        return


    marker_path: Optional[Path] = None
    marker_payload: Optional[dict] = None
    resume_result_path: Optional[Path] = None
    if should_optimize and args.campaign_rounds > 1:
        rounds_payload = _run_plain_optimize_rounds(
            config_path=config_path,
            cfg=cfg,
            base_output_dir=base_output_dir,
            camera_name=camera_name,
            round_count=int(args.campaign_rounds),
            resume_from_result=bool(args.resume_from_result),
            project_root=root,
        )
        best_round = rounds_payload["best_round"] or {}
        print("Rounds summary JSON:", rounds_payload["summary_json"])
        print("Rounds output dir:", rounds_payload["rounds_output_dir"])
        print("Completed rounds:", rounds_payload["round_count_completed"])
        print("Best round index:", best_round.get("round_index"))
        print("Best score:", best_round.get("best_score"))
        print("Best image:", best_round.get("best_image"))
        print("Best result JSON:", best_round.get("result_json"))
        camera_history_summary_path, camera_history_summary = _write_camera_history_summary(camera_name, project_root=root)
        camera_history_summary_compact_path = _write_camera_history_summary_compact(
            camera_name,
            camera_history_summary,
            project_root=root,
        )
        _print_camera_history_summary(camera_history_summary, camera_history_summary_path)
        _print_camera_history_summary_compact(camera_history_summary_compact_path)
        if args.print_summary_json:
            best_result_json_path = Path(best_round["result_json"]).resolve()
            _emit_cli_summary_json(
                _build_cli_summary_payload(
                    camera_name=camera_name,
                    config_path=config_path,
                    mode="plain_optimize_rounds",
                    result_json_path=best_result_json_path,
                    result_payload=_load_json_if_exists(best_result_json_path) or {},
                    summary_json_path=Path(rounds_payload["summary_json"]).resolve(),
                    rounds_output_dir=Path(rounds_payload["rounds_output_dir"]).resolve(),
                )
            )
        return

    if should_optimize:
        marker_path = _marker_path_for_output_dir(base_output_dir, project_root=root)
        if args.resume_from_result:
            resume_result_path = _read_latest_result_path(marker_path, base_output_dir)
        cfg["output_dir"] = str(_build_isolated_output_dir("run", camera_parent=camera_name, project_root=root))
        marker_payload = {
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": str(config_path),
            "base_output_dir": str(base_output_dir),
            "output_dir": str(cfg["output_dir"]),
            "max_iters": int(cfg.get("max_iters", 0)),
            "resume_from_result": bool(args.resume_from_result),
            "status": "starting",
        }
        _write_run_marker(marker_path, marker_payload)
    else:
        cfg["output_dir"] = str(base_output_dir)

    live_log_path = _configure_live_log(cfg, args.resume_from_result, project_root=root)
    print("Live log:", str(live_log_path))
    if should_optimize:
        print("Isolated output dir:", str(cfg["output_dir"]))

    if marker_path is not None and marker_payload is not None:
        marker_payload["status"] = "running"
        marker_payload["live_log"] = str(live_log_path)
        _write_run_marker(marker_path, marker_payload)

    from src.calibration.camera_calibration import CameraCalibrator
    calib = CameraCalibrator(cfg, config_path=config_path)
    calib.live_log_path = live_log_path
    setattr(calib, "print_progress_json", bool(args.print_progress_json))
    calib._calib_max_iters = int(cfg.get("max_iters", 0))
    calib._calib_round_index = 1
    calib._calib_round_count = 1
    calib._calib_overall_total_iters = int(cfg.get("max_iters", 0))
    calib._calib_phase = "explore"
    # DDE capture_initial_values 已移除：初始值只从 vehicle 文件获取。
    # 如果需要恢复 DDE 覆盖，取消下方注释：
    # if not args.resume_from_result and should_optimize:
    #     initial_values = calib.capture_initial_values()
    #     for p in calib.params:
    #         if p.name in initial_values:
    #             p.value = initial_values[p.name]
    #     for name, value in initial_values.items():
    #         if name in cfg.get("parameters", {}):
    #             cfg["parameters"][name]["initial"] = value
    try:
        if args.propose_boards:
            calib.propose_boards_config(
                args.config,
                output_path=args.proposal_output,
                preview_path=args.proposal_preview,
            )
            return

        if args.annotate_image:
            annotated_path, board_scores = calib.annotate_existing_image(
                Path(args.annotate_image),
                Path(args.annotate_output) if args.annotate_output else None,
            )
            print("Annotated image:", str(annotated_path))
            for score in board_scores:
                print(
                    f"{score.board_id}: score={score.total_score:.6f} compared={score.compared} "
                    f"failed_reason={score.failed_reason}"
                )
            return

        if args.capture_initials:
            values = calib.capture_initial_values()
            print("Captured current values from CarMaker GUI:")
            for name, value in sorted(values.items()):
                print(f"  {name}: {value}")
            print("Note: These values are not written to config file. Vehicle file is the single source of truth.")
            return

        if args.resume_from_result:
            calib.load_best_values_from_result(
                resume_result_path or (base_output_dir / "result.json")
            )

        result = calib.optimize()
        wb_result = _write_best_values_to_vehicle_config(
            config_path,
            cfg,
            camera_name,
            float(result["best_score"]),
            result["best_values"],
            project_root=root,
        )
        if wb_result is None:
            print(
                "WARNING: Vehicle writeback failed — results will NOT persist. "
                "Next calibration will start from original vehicle defaults."
            )
        else:
            _verify_vehicle_writeback(
                config_path, cfg, camera_name, wb_result,
                result["best_values"],
                project_root=root,
            )
        if marker_path is not None and marker_payload is not None:
            marker_payload.update(
                {
                    "status": "finished",
                    "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "best_score": result["best_score"],
                    "best_values": result["best_values"],
                    "best_image": result["best_image"],
                    "result_json": str(Path(cfg["output_dir"]) / "result.json"),
                    "run_session_id": result.get("run_session_id"),
                }
            )
            _write_run_marker(marker_path, marker_payload)
        print("Best score:", result["best_score"])
        print("Best values:", result["best_values"])
        print("Best image:", result["best_image"])
        if result.get("best_score_image"):
            print("Best score image:", result["best_score_image"])
        if result.get("best_overlay_image"):
            print("Best overlay image:", result["best_overlay_image"])
        run_stats = result.get("run_stats") or {}
        if run_stats:
            print(
                "Run stats: "
                f"calibration_count={run_stats.get('calibration_count')} "
                f"total_elapsed={run_stats.get('total_elapsed_text')} "
                f"average_elapsed={run_stats.get('average_elapsed_text')}"
            )
        print("Result JSON:", str(Path(cfg["output_dir"]) / "result.json"))
        camera_history_summary_path, camera_history_summary = _write_camera_history_summary(camera_name, project_root=root)
        camera_history_summary_compact_path = _write_camera_history_summary_compact(
            camera_name,
            camera_history_summary,
            project_root=root,
        )
        _print_camera_history_summary(camera_history_summary, camera_history_summary_path)
        _print_camera_history_summary_compact(camera_history_summary_compact_path)
        if args.print_summary_json:
            result_json_path = (Path(cfg["output_dir"]) / "result.json").resolve()
            _emit_cli_summary_json(
                _build_cli_summary_payload(
                    camera_name=camera_name,
                    config_path=config_path,
                    mode="single_run",
                    result_json_path=result_json_path,
                    result_payload=result,
                )
            )
    except Exception as exc:
        if marker_path is not None and marker_payload is not None:
            marker_payload.update(
                {
                    "status": "failed",
                    "failed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "error": str(exc),
                }
            )
            _write_run_marker(marker_path, marker_payload)
        raise


if __name__ == "__main__":
    main()
