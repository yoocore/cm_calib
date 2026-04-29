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
- pip install pywinauto pillow opencv-python numpy

Recommended run environment
- Windows scaling 100%
- Fixed monitor layout and resolution
- Keep IPGMovie online and responsive
- DDE to CarMaker and IPG-MOVIE must be available
- After the lens page has been initialized once, Script Control, Camera Settings, and IPGMovie windows may remain minimized during parameter write and FBO capture smoke runs

Quick start
1) Edit the camera-specific config, for example config.rear_tv.json.
2) Set real_image/output_dir.
3) Configure boards[] for all visible marker boards.
4) Verify the active Script Control command path points to Data/Script/CameraCalibration/script_control_apply.tcl.
5) Optional: read current Script Control values back into config:
  python camera_calibration.py --config config.rear_tv.json --capture-initials --write-initials-to-config
6) Run optimization:
  python camera_calibration.py --config config.rear_tv.json --resume-from-result

Optional override
- Always pass --config explicitly, for example --config config.rear_tv.json.
- When you add more viewpoints later, keep using config.<camera>.json naming, for example config.front_tv.json.

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

Output
- Screenshots: output_dir/*.png
- Optimization result: output_dir/result.json

Scoring notes
- Score is lower when match is better.
- Current implementation supports:
  - named checkerboard boards (for boards such as B1-B4 and S1-S5)
  - custom_groundmaker boards (for regions such as G1_left/G1_center/G1_right)
- Each board is scored independently using RMSE/max_error/miss_rate.
- Final score is weighted sum across boards plus degradation penalty.
- If a critical board degrades too much, the trial is rejected.
- target_score in config controls stop threshold.

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
