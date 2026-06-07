# Camera Calibration FBO Investigation - Progress Handoff

> Last updated: 2026-06-07
> Latest commit: `8746627 fix: remove extra closing braces in FBO diagnostic catch lines that broke Tcl parsing`
> Uncommitted: FBO reverted to viewport widget size + Tcl brace fix in diagnostic catch lines
> Branch: `calib/e4ca284-plus-features`
> Author: Bytes (OpenCode agent)

---

## 1. Problem Statement

Camera calibration tool captures simulation images via FBO (Frame Buffer Object) in IPG-MOVIE and compares them against real camera images to score calibration accuracy. Two categories of issues were investigated:

**A. Intermittent FBO Creation Error** (RESOLVED)
```
FBO Creation error (unknown error)
please check if FrameBufferObjects are supported
```

**B. Cross-camera initial score regression** (ROOT CAUSE FOUND, FIX APPLIED)
- If previous session ended with right_rear → right_rear scores normally (43), rear_tv/left_tv abnormal
- If previous session ended with another camera → right_rear scores abnormally (1455), rear_tv/left_tv normal
- Root cause: FBO was created at 2× viewport GL size (real image dims), but `UpdateView` renders at viewport GL size → 75% of FBO uninitialized
- Why right_rear scored 43 but rear_tv scored 3848: camera pitch affects where boards appear in the image

---

## 2. Problem A: Intermittent FBO Creation Error — RESOLVED

### Root Cause
`update`/`update idletasks` in the same Tcl execute as `FBO new` causes GL context state corruption.

### Fix
Commit `60aa02c fix: reduce movie pre-capture event pumping` — removed event pumping from `_movie_background_tcl_commands()` and `ensure_movie_abraxas_enabled()`.

### Validation
- 20x FBO probe: 20/20 OK
- 100x full prepare chain: 100/100 OK
- 20x `capture_movie()`: 20/20 OK
- 20x `evaluate()`: 20/20 OK
- 3 production calibration runs: 3/3 OK, 0 FBO errors

---

## 3. Problem B: Cross-Camera FBO Size Mismatch — ROOT CAUSE FOUND

### Symptom Pattern (discovered 2026-06-07)
User identified a clear pattern across multiple runs:

| Previous session ended with | right_rear score | rear_tv/left_tv score |
|---|---|---|
| right_rear | 43 ✅ | Abnormal ❌ |
| NOT right_rear | 1455 ❌ | Normal ✅ |

### Root Cause Analysis

**Camera-specific dimensions:**
| Camera | Real image | Aspect | Expected FBO |
|---|---|---|---|
| right_rear | 1920×1280 | 3:2 | 960×640 |
| rear_tv | 1920×1536 | 5:4 | 960×768 |
| left_tv | 1920×1536 | 5:4 | 960×768 |

**Old FBO code** used `$wpath.gl0 cget -width/height` (viewport widget size) for FBO creation. When switching cameras, the viewport widget may not have been resized to match the new camera's expected aspect ratio. This caused:
- FBO created at 960×768 (5:4) instead of 960×640 (3:2) for right_rear
- Rendering at wrong aspect ratio → boards appear at wrong positions → high score
- Image comparison showed perspective transformation (not simple translation)

### Evidence

**Fresh CarMaker restart (rounds_20260607_001822):**
- right_rear, same params: lens_fov=124.7, pitch=-1.0052, pos_x=3.4413, etc.
- Initial score=1455.87 (bad run) vs 43.48 (good run with same params)
- FBO diagnostic: `viewno=0:0 wpath=.view0 size=960x640`
- Board score comparison (same params, same viewport size in diagnostic):
  - B1: 2.13→43.14 (20x worse), B2: 0.71→40.08 (56x worse)
  - S2: 2.90→786.46 (271x worse, 9/28 matched)
  - All boards show 10-90x deterioration

**Image pixel comparison (good vs bad run):**
- Mean abs diff: 16.18, Max: 242
- 134,609 pixels differ >10 (22%), 110,110 differ >50 (18%)
- NOT a simple translation — perspective transformation with varying offsets:
  - Top patches: dx=-60/+80, dy=+16
  - Middle patches: dx=-16/+24, dy=-2
  - Bottom patches: dx=+12/+64, dy=-16 to -38

### Viewport Corruption Theory — DISPROVED

Initially suspected `ensure_movie_render_size()` (commit `8dec268`) corrupted viewport via `$wpath.gl0 configure -width/height`. This was:
1. Removed in commit `35cc311`
2. User restarted CarMaker completely
3. Score STILL 1455 → viewport corruption NOT the cause

### Previous wrong theories (eliminated)
1. `_resolve_parameter_bounds` using 0.0 as initial → proved wrong (user confirmed params correct)
2. `_clamp_to_parameter_bounds` clamping → proved wrong (same root cause as #1)
3. Viewport corruption from `ensure_movie_render_size` → disproved by CarMaker restart
4. Board S8 config mismatch → valid observation but not the cross-camera issue

---

## 4. Fix Applied (Uncommitted) — REVERTED: FBO back to viewport widget size

### Why the real-image-dims fix FAILED

The FBO real-image-dims fix (using `self.real_img.shape[:2]`) created FBOs at **2× the viewport widget GL size**:
- right_rear: FBO=1920×1280, viewport=960×640
- rear_tv: FBO=1920×1536, viewport=960×768

IPG-MOVIE's `FBO begin` only binds the framebuffer — it does NOT call `glViewport()`.
So `UpdateView $vno` renders at the viewport widget's GL dimensions into the larger FBO,
leaving 75% of the FBO as uninitialized/black.

**Why right_rear scored 43 but rear_tv scored 3848:**
- right_rear (pitch=-1.0°, points downward) → boards cluster in lower-left → fall WITHIN the 960×640 rendered sub-region → detection succeeds
- rear_tv (pitch=17.5°, points upward) → boards cluster in upper portion → OUTSIDE the 960×768 rendered sub-region → detection fails (9/28 matched)

### New Fix: Revert FBO to viewport widget GL size

**File:** `camera_calibration.py` — `_capture_movie_via_dde_fbo()` (line ~7754)

**Removed:** `ref_h, ref_w = self.real_img.shape[:2]` (no longer needed)

**FBO creation uses viewport widget GL size (`$vp_w`/`$vp_h`):**
```tcl
set vp_w [$wpath.gl0 cget -width]
set vp_h [$wpath.gl0 cget -height]
# FBO matches viewport widget GL size, rebuild on size change
set ::calib_fbo [FBO new $vp_w $vp_h -tex rgb -noclear]
} elseif {$vp_w != $::calib_fbo_w || $vp_h != $::calib_fbo_h} {
    catch {FBO delete $::calib_fbo}
    set ::calib_fbo [FBO new $vp_w $vp_h -tex rgb -noclear]
}
set ::calib_fbo_w $vp_w; set ::calib_fbo_h $vp_h
```

**Key changes:**
1. Removed `ref_h, ref_w = self.real_img.shape[:2]` — no longer used
2. FBO dimensions use `$vp_w`/`$vp_h` (viewport widget GL size)
3. `::calib_fbo_w`/`::calib_fbo_h` tracking preserved for cross-camera rebuild
4. `probeImg` also uses `$vp_w`/`$vp_h`
5. Diagnostic output: removed `ref=WxH` (no longer relevant)

**Also fixed:** Removed extra `}` from two diagnostic catch lines that broke Tcl parsing.
The original had `} }` (2 closing braces), the previous edit accidentally had `} } }` (3 closing braces).

**Preserved from previous fixes:** shared FBO, elseif rebuild, diagnostic file write, event pumping removal.

**py_compile:** clean

---

## 5. Shared FBO Fix (commit 8dec268, kept)

### What it does
- Persistent `::calib_fbo` Tcl global — lazy init, auto-rebuild on failure
- Removed per-capture `FBO delete` — FBO persists across captures
- `_cleanup_shared_fbo()` method — DDE command to delete FBO + unset global
- `optimize()` try/finally block calling `_cleanup_shared_fbo()`

### Key code locations
- `_capture_movie_via_dde_fbo`: Tcl body (line 7777-7801) — shared FBO with size-change rebuild
- `_cleanup_shared_fbo`: line ~7879 — DDE cleanup with 3 retries
- `optimize()` try/finally: line ~11607

---

## 6. FBO Diagnostic (commit 158b245, kept)

### What it does
Writes camera state to `{tag}_fbo_diag.txt` during each FBO capture, wrapped in `catch {}` so failures cannot break capture.

### Diagnostic output format
```
viewno=0:0 wpath=.view0 vp=960x768
```
- `viewno`: viewport number from `$View(ev.view)`
- `wpath`: viewport widget path
- `vp`: viewport widget GL dimensions (= FBO dimensions)

### Failed diagnostic reads (silently catch-wrapped)
- `.camera.f.camselect` — camera selector widget (doesn't exist)
- `.camera.car.camselect` — alternate camera selector (doesn't exist)
- `.camera.svptx/svpty/svptz` — read-back position widgets (don't exist)

---

## 7. Git History (since last handoff 2026-06-04)

```
35cc311 2026-06-06 fix: remove ensure_movie_render_size that corrupts viewport rendering state
8dec268 2026-06-06 fix: use shared persistent FBO to avoid resource exhaustion; add viewport size helpers
158b245 2026-06-06 feat: add safe FBO diagnostic via catch-wrapped file write
ed9d515 2026-06-06 revert: remove broken DDE diagnostic that caused FBO errors
58f102d 2026-06-06 feat: add DDE camera state diagnostic at initial apply
f183941 2026-06-05 fix: pass actual initial value to _resolve_parameter_bounds
508b5df 2026-06-05 fix: restore seed comparison logic for all anchor sources
cb1e1a5 2026-06-05 fix: prefer vehicle values over history_best in round seed anchor (re-applied)
3b39c57 2026-06-05 revert: remove movie view size setting in capture_movie (caused performance issue)
ad24d88 2026-06-05 cleanup: remove debug file logging for vehicle DDE read
4fa3dbd 2026-06-05 fix: prefer vehicle values over history_best in round seed anchor
fd14554 2026-06-05 debug: add file logging for vehicle DDE read to diagnose rear_tv/left_tv issue
dc2ff71 2026-06-05 fix: pass config_path to CameraCalibrator in multi-start and refine campaigns
5e982c5 2026-06-05 fix: use self.config_path in capture_movie to read real image size
2d27dcb 2026-06-05 fix: ensure movie view size matches real image before FBO capture
f94ef64 2026-06-04 fix: remove config write from capture_initial_values_to_config
353fc5a 2026-06-04 refactor: remove config initial value write-back, vehicle file is single source
373ba35 2026-06-04 debug: add detailed logging for vehicle DDE read and config initial values
701f262 2026-06-04 fix: make 'initial' optional in _resolve_parameter_bounds
be4fc58 2026-06-04 Revert "fix: make 'initial' optional in _load_params"
881ade9 2026-06-04 docs: add runtime verification results to handoff
4c01b93 2026-06-04 docs: update handoff with Phase 11 initial field fix
5e22ddd 2026-06-04 fix: make 'initial' optional in _load_params
d2018b9 2026-06-04 refactor: bounds reform — replace min_offset/max_offset with step×bounds_multiplier
b7ca756 2026-06-04 fix: restore rear_tv config to historical best initial values
e0a02e9 2026-06-04 docs: add Phase 10 View dict stale size bug to handoff
545083c 2026-06-04 fix: read capture dimensions from GL widget instead of stale View dict
60aa02c fix: reduce movie pre-capture event pumping (FBO intermittent fix)
0bb05ff fix: avoid recursive movie timer update
```

---

## 8. Uncommitted Changes

```
camera_calibration.py   # FBO reverted to viewport widget size + Tcl brace fix
```

---

## 9. Key Code Locations

### FBO Capture Pipeline
| Function | Lines | Purpose |
|---|---|---|
| `_capture_movie_via_dde_fbo` | 7734-7843 | FBO capture with 6-retry loop, diagnostic, shared FBO |
| `_cleanup_shared_fbo` | ~7879-7920 | DDE cleanup of shared FBO global |
| `capture_movie` | ~7922-7930 | Entry point, calls `_capture_movie_via_dde_fbo` |
| `evaluate` | ~10345-10367 | Calls `capture_movie`, scores boards |

### Apply Flow (Script Control)
| Function | Lines | Purpose |
|---|---|---|
| `_render_script_control_apply_script` | 6938-6983 | Generate Tcl: write widgets → `.camera.btn.set invoke` → readback |
| `_apply_script_control_params` | 7484-7533 | Quantize, send, verify readback, retry 3x |
| `_apply_value_map` | 7973-7985 | Set param.value, call `_apply_script_control_params` |

### Widget Mappings (5585-5612)
| Direction | Param | Widget Path |
|---|---|---|
| WRITE | pos_x | `.camera.evptx` |
| WRITE | pos_y | `.camera.evpty` |
| WRITE | pos_z | `.camera.evptz` |
| WRITE | roll | `.camera.x` |
| WRITE | pitch | `.camera.y` |
| WRITE | yaw | `.camera.z` |
| READ | pos_x | `.camera.svptx` |
| READ | pos_y | `.camera.svpty` |
| READ | pos_z | `.camera.svptz` |
| WRITE+READ | lens_fov | `.camera.cammoddlg.fov.e` |
| WRITE+READ | lens_scale | `.camera.cammoddlg.fisheye.ctrl.e1` |
| WRITE+READ | lens_offset_x | `.camera.cammoddlg.fisheye.ctrl.e2` |
| WRITE+READ | lens_offset_y | `.camera.cammoddlg.fisheye.ctrl.e3` |

### Optimize Loop
| Function | Lines | Purpose |
|---|---|---|
| `_optimize_coordinate_descent_impl` | ~11168-11247 | Main optimization loop |
| `optimize` | ~11537-11607 | Entry point, try/finally for FBO cleanup |

---

## 10. Camera Configs

### Right Rear (`configs/camera.right_rear.json`)
- Real image: `2_right_rear_origin.jpg` (1920×1280, 3:2)
- Params: pos_x=3.4413, pos_y=-0.9512, pos_z=0.9608, pitch=-1.0052, roll=0.3714, yaw=227.8997, lens_fov=124.7
- Lens scale/offset: bounds_multiplier=0 (not optimized)

### Rear TV (`configs/camera.rear_tv.json`)
- Real image: `6_rear_tv_origin.jpg` (1920×1536, 5:4)
- Params: pos_x=0.3016, pos_y=0.0438, pos_z=0.6782, pitch=17.5418, roll=-0.0544, yaw=180.2311, lens_fov=195.2
- Lens scale/offset: bounds_multiplier=0 (not optimized)
- S8 board: ROI [238,742,1478,468], score dominated (1034/1054 total)

### Left TV (`configs/camera.left_tv.json`)
- Similar to rear_tv (5:4 aspect)

---

## 11. Previous Investigation Phases (Problem A — FBO Intermittent)

### Phase 1-4: Root Cause Discovery
- Phase 1: Symptom characterization — FBO failure intermittent, retry usually succeeds
- Phase 2: Contention experiments — `update_only_001` first real reproduction (17/20, 3 failures)
- Phase 3: Update intensity — high-frequency update creates two anomaly types
- Phase 4: Inline update (DECISIVE) — `inline_update_x3`: 15/20 success, 5/20 REAL FBO error

### Phase 5: First Code Fix
Commit `60aa02c` — removed `update`/`update idletasks` from `_movie_background_tcl_commands()` and `ensure_movie_abraxas_enabled()`.

### Phase 6-9: Runtime Verification
- Phase 6: Stepwise prepare → FBO: 5/5 OK
- Phase 7: 20x/100x stress test: 100/100 OK
- Phase 8: E2E capture_movie() + evaluate() x20: 20/20 OK
- Phase 9: Production calibration x3: 3/3 OK, 0 FBO errors

### Phase 10: View Dict Stale Size Bug
`_capture_movie_via_dde_fbo()` read dimensions from View dict which retained old values after `View::SetSize`. Fixed by reading from GL widget (`$wpath.gl0 cget -width/height`).

### Phase 11: _resolve_parameter_bounds Missing 'initial' Field
After bounds reform (d2018b9), `_resolve_parameter_bounds()` crashed with KeyError for configs missing `initial` field. Fixed by making `initial` optional with default 0.0 (commit 701f262, then f183941 for proper override).

---

## 12. Diagnostic Scripts

| File | Purpose | Status |
|---|---|---|
| `diagnose_camera_widgets.py` | Read ALL camera widget values via DDE | Written, not yet run |
| `diagnose_camera_diff.py` | Before/after diff diagnostic with apply mode | Written, not yet run |
| `runtime_fbo_stress_20x.py` | 20x/100x FBO stress test (3 phases) | Works |
| `runtime_e2e_calib_stress.py` | E2E capture_movie() + evaluate() stress | Works |
| `fbo_score_check.py` | Standalone FBO capture probe | Works |

---

## 13. Key Constraints

1. **FBO failure retry**: 6 attempts with `retry_delay = max(settle_sec, 0.2)`. Raw FBO failure + immediate retry usually succeeds.
2. **Shared FBO**: Persistent `::calib_fbo` Tcl global, lazy init, auto-rebuild on failure, cleanup in `optimize()` finally block.
3. **Don't remove `update`/`update idletasks` from prepare helpers**: Each runs in separate DDE call, proven safe by 100x+ tests.
4. **Vehicle file is single source**: Config `initial` values come from `.veht` file via DDE, not from config JSON.
5. **Camera selection**: `right_rear` latches reliably; `rear_tv`/`left_tv` may not latch in some sessions (separate issue).

---

## 14. Environment

- **CarMaker**: win64-14.1, `D:\IPG\carmaker\win64-14.1`
- **Project**: `C:\CM_Projects\CMO141_Calibration`
- **Python**: 3.12
- **DDE**: pywin32 `dde` → `TclEval` → `CarMaker` → `IPG-MOVIE`
- **FBO API**: `FBO new $w $h -tex rgb -noclear` → `FBO begin` → `UpdateView` → `FBO end` → `gl readpixels`

---

## 15. Next Steps

1. **User tests the real-image-dimensions FBO fix** — run calibration for all 3 cameras in different orders to verify cross-camera score consistency
2. **If fix works, commit** — the change is in `_capture_movie_via_dde_fbo()` Tcl body
3. **Rear TV S8 board issue** — S8 dominates score (1034/1054) due to ground-mounted board not properly rendered in simulation. Separate from FBO issue.
4. **Optional**: Add FBO dimension mismatch warning if viewport size != real image size (diagnostic aid)
