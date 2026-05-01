Camera Calibration Loop (manual precondition)

Purpose
- Assume you already did this manually:
  - Open IPGMovie
  - Fix to camera view
  - Open Camera Settings and open the lens page at least once so `.camera.cammoddlg` widgets are created
- Then this script only does:
  - Push installation parameters through Script Control DDE
  - Capture IPGMovie offscreen image through FBO
  - Detect multiple boards in simulation and real image
  - Compare by aggregated multi-board geometric score
  - Iterate until score is good enough

Files
- camera_calibration.py: main script
- config.<camera>.json: one maintained calibration config per camera, for example config.rear_tv.json
- script_control_apply.tcl: single maintained Script Control command script

Dependencies
- Python 3.9+
- Install from the maintained local dependency file:
  - from repository root: python -m pip install -r Data/Script/CameraCalibration/requirements.txt
  - from Data/Script/CameraCalibration: python -m pip install -r requirements.txt

Interpreter and environment
- The maintained dependency file lives in Data/Script/CameraCalibration/requirements.txt.
- The recommended interpreter for this workspace is the project-local .venv at .venv/Scripts/python.exe.
- In VS Code, select the workspace interpreter once and keep it pinned to .venv.
- If imports such as cv2/numpy/PIL suddenly show unresolved again, first verify the active interpreter rather than debugging the script.
- When running commands manually, prefer the explicit interpreter path:
  - c:/CM_Projects/CMO141_Calibration/.venv/Scripts/python.exe camera_calibration.py --config config.rear_tv.json

Recommended run environment
- Windows scaling 100%
- Fixed monitor layout and resolution
- Keep IPGMovie online and responsive
- DDE to CarMaker and IPG-MOVIE must be available
- After the lens page has been initialized once, Script Control, Camera Settings, and IPGMovie windows may remain minimized during parameter write and FBO capture smoke runs

Quick start
1) Edit the camera-specific config, for example config.rear_tv.json.
2) Create or refresh the local virtual environment, then install dependencies from requirements.txt.
3) In VS Code, confirm the selected interpreter is .venv/Scripts/python.exe.
4) Set real_image/output_dir.
5) Configure boards[] for all visible marker boards.
6) Verify the active Script Control command path points to Data/Script/CameraCalibration/script_control_apply.tcl.
7) Optional: read current Script Control values back into config:
  python camera_calibration.py --config config.rear_tv.json --capture-initials --write-initials-to-config
8) Default optimization run from config initial values:
  python camera_calibration.py --config config.rear_tv.json
9) Optional multi-start short sprint when you want to test for another nearby basin:
  python camera_calibration.py --config config.rear_tv.json --multi-start-count 4 --multi-start-iters 24 --multi-start-jitter-steps 2.0
10) Optional one-command campaign when you want short exploration first and then long refinement from the best explored start:
  python camera_calibration.py --config config.rear_tv.json --explore-then-refine --multi-start-count 4 --multi-start-iters 24 --refine-iters 180
  11) Successful optimization runs now auto-write best_values back into the config initial fields for the next run.

Optional override
- Always pass --config explicitly, for example --config config.rear_tv.json.
- When you add more viewpoints later, keep using config.<camera>.json naming, for example config.front_tv.json.
- --resume-from-result is still available as a legacy/manual recovery mode, but it is no longer the recommended default workflow.
- --explore-then-refine uses --multi-start-* as the exploration phase controls and then starts one refinement run from the best short-run result.
- In --explore-then-refine mode, omitting --multi-start-count defaults to 4 starts and omitting --multi-start-iters defaults to min(config max_iters, 24).
- The default single run, multi-start mode, and explore-then-refine mode all persist their final best_values back into the input config as the next initial values.
- --capture-initials --write-initials-to-config is still useful when you want to sync the config from the currently loaded IPG-MOVIE values before any optimization run.

Simplified repository layout
- The repository now keeps one camera-specific JSON config per viewpoint, named config.<camera>.json.
- The repository keeps exactly one maintained Script Control Tcl: Data/Script/CameraCalibration/script_control_apply.tcl.
- The Python path is intentionally narrowed to Script Control DDE for parameter writes and IPG-MOVIE dde_fbo for capture.
- The repository no longer keeps UI window connection logic or alternate movie capture modes; the active path is pure DDE/FBO.
- Old overnight/best/final/proposed config variants are intentionally removed from versioned inputs.

Automatic view-specific board proposal
- You can auto-generate a candidate boards config from the current real_image:
  python camera_calibration.py --config config.rear_tv.json --propose-boards
- This writes:
  - a proposed config next to the input config: *.proposed.json
  - a numbered preview image under output_dir/board_proposal_preview.png
- Current behavior:
  - custom_groundmaker is proposed as a single detected instance
  - checkerboards are proposed as repeated visible 7x3 candidates for the current view
  - if multiple logical board families share the same checkerboard size, the proposal currently emits generic checkerboard instances and still expects a quick human confirmation

Bootstrap a new camera config from a manual annotation image
- When a new camera viewpoint already has:
  - the real image, for example Movie/ngxpro/8_left_tv_origin.jpg
  - a manually marked image with red rectangles around visible boards
- You can generate a new config by reusing the current --config as a template:
  python camera_calibration.py --config config.rear_tv.json --bootstrap-config-from-annotation --bootstrap-real-image C:/CM_Projects/CMO141_Calibration/Movie/ngxpro/8_left_tv_origin.jpg --bootstrap-annotated-image C:/CM_Projects/CMO141_Calibration/Movie/ngxpro/8_left_tv.jpg
- Default behavior:
  - copies non-board settings from the template config
  - replaces real_image with --bootstrap-real-image
  - extracts red rectangles from --bootstrap-annotated-image as board ROI
  - groups rectangles by size into B, S, and G1 families
  - reads the current active IPG-MOVIE camera window values through Script Control and writes them into parameters.*.initial
  - writes a new config next to the template, for example config.left_tv.json
  - writes a verification preview image to SimOutput/<camera>/annotation_bootstrap_preview.png
- Optional overrides:
  - --bootstrap-output for the generated JSON path
  - --bootstrap-preview for the preview image path
  - --bootstrap-camera-name when the target camera name should not be derived from the image filename
  - --bootstrap-skip-current-params when you only want ROI/template bootstrap and do not want to read current window values

Output
- Screenshots: output_dir/*.png
- Best score image: whenever a new global best is written, the script also writes a scored overlay image next to it as *_score.png
- Optimization result: output_dir/result.json
- result.json now includes acceptance, which records whether the calibration passed by target_score or by bottleneck fallback thresholds

Scoring notes
- Score is lower when match is better.
- Current implementation supports:
  - named checkerboard boards (for boards such as B1-B4 and S1-S5)
  - custom_groundmaker boards (for regions such as G1_left/G1_center/G1_right)
- Each board is scored independently using RMSE/max_error/miss_rate.
- Final score is weighted sum across boards plus degradation penalty.
- If a critical board degrades too much, the trial is rejected.
- target_score in config controls stop threshold.
- Final acceptance is evaluated after optimization completes:
  - pass immediately if best_score <= target_score
  - if target_score is not reached but optimization appears bottlenecked, still pass when compared-board max score < acceptance_criteria.bottleneck_board_score_max_threshold and compared-board average score < acceptance_criteria.bottleneck_board_score_avg_threshold
  - if neither condition is met, the run is marked as not passed in result.json and campaign summaries

Optimization notes
- Every parameter already goes through single-parameter bidirectional probing in the main loop.
- joint_exploration is narrower by default and only applies to joint_exploration.param_names.
- If a camera is better served by joint exploration on every parameter, set joint_exploration.apply_to_all_params to true.
- apply_to_all_params keeps the existing selected-param behavior as the default, so view-specific configs like rear_tv can stay conservative.

boards[] config notes
- Every board entry requires:
  - board_id
  - board_type
  - critical
  - weight
  - roi
- checkerboard board also requires board_size = [cols, rows] (inner corners count)
- custom_groundmaker board requires template_image
- degrade_threshold_* fields define per-board anti-regression guardrails
- The script resizes screenshots to the reference image size before scoring
- Reference image must allow detection for all critical boards

Limitations
- Lens parameters still depend on `.camera.cammoddlg` existing inside IPG-MOVIE; opening the lens page once is currently required.
- If some parameters are not real-time in IPGMovie, convergence will be poor.
- custom_groundmaker detection currently uses ORB/template-based anchors.
- ChArUco/Aruco are described in the design docs but are not implemented in the script yet.

Tip
- There is also a Tcl example for camera selection in
  Data/Script/Examples/RemoteControlIPGMovie.tcl
  You can combine that with this RPA flow if needed.
