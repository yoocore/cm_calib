# IPG-MOVIE Intermittent FBO Failure - Progress Handoff

> Last updated: 2026-06-04  
> Latest commit: `60aa02c fix: reduce movie pre-capture event pumping`  
> Author: Bytes (OpenCode agent)

---

## 1. Problem Statement

IPG-MOVIE / CarMaker 标定链路存在间歇性 `FBO Creation error (unknown error)`，错误消息包含：

```
FBO Creation error (unknown error)
please check if FrameBufferObjects are supported
```

该错误在 capture 流程中随机出现，不是 100% 复现。递归卡死主线已修复（commit `0bb05ff`），当前主问题是剩余的易失性 FBO/GL 失败。

---

## 2. Root Cause (Established)

### Primary Finding

**在同一 Tcl execute 内，`FBO new` 之前执行 `update` 或 `update idletasks` 本身就足以触发真实 `FBO Creation error`。第二客户端并发不是必要条件。**

### Experimental Evidence

通过三轮受控实验（仓库外临时脚本）收敛：

| 模式 | 成功率 | 失败类型 |
|------|--------|----------|
| baseline（无 update） | 20/20 | 无 |
| inline_update_once | 20/20 | 无 |
| **inline_update_x3** | **15/20** | **5/20 真实 FBO error** |
| **inline_update_x10** | **16/20** | **4/20 真实 FBO error** |
| **inline_idletasks_once** | **15/20** | **5/20 真实 FBO error** |
| inline_update_then_idletasks | 19/20 | 1/20 真实 FBO error |

关键观察：
- 失败时耗时很短（0.18–0.35s），与后台争用实验中常见的 2.8–3.0s 长耗时空成功不同
- 单个 `update` 前置仍可稳定（20/20），但 3 个以上或 `update idletasks` 就能触发
- 第二客户端并发只是放大器，改变异常表型（DDE failure / 空成功），但不是必要条件

### Mechanism

"pre-capture event pumping" 会让 GL/FBO 上下文或状态进入不稳定相位。具体来说：

- `update` / `update idletasks` 推进 Tk 事件循环
- 事件循环推进可能触发 GL context 的隐式状态变更
- 紧接着 `FBO new` 时，GL 驱动处于不一致状态，导致 FBO 创建失败

---

## 3. Code Changes (Commit 60aa02c)

### 3.1 Production Changes

**File: `cmapi_testrun_control.py`**

#### Change 1: `_movie_background_tcl_commands()` (line 88)

**Before:** 末尾包含 `update` / `update idletasks`

```python
# Lines 88-109 (AFTER edit)
def _movie_background_tcl_commands(*, include_root: bool = True) -> list[str]:
    commands: list[str] = []
    if include_root:
        commands.extend([
            'catch {wm attributes . -topmost 0}',
            'catch {wm lower .}',
        ])
    commands.extend([
        'if {[winfo exists .camera]} {',
        '    catch {wm attributes .camera -topmost 0}',
        '    catch {wm lower .camera}',
        '}',
        'if {[winfo exists .camera.cammoddlg]} {',
        '    catch {wm attributes .camera.cammoddlg -topmost 0}',
        '    catch {wm lower .camera.cammoddlg}',
        '}',
    ])
    return commands
    # NOTE: previously had 'update' and 'update idletasks' here — removed
```

**Impact:** This helper is called by `ensure_movie_camera_selected()`, `ensure_movie_camera_widgets()`, and `ensure_movie_camera_dialogs_normal()`. Removing the global event pump from here eliminates unnecessary queue flushing from three downstream helpers.

#### Change 2: `ensure_movie_abraxas_enabled()` (line 1773)

**Before:** After menu invoke, had `UpdateView`, `<Expose>`, and a second round of `update`/`update idletasks`

```python
# Lines 1790-1800 (AFTER edit)
[
    'if {![info exists View(ev.view)]} {error "missing View(ev.view)"}',
    'scan $View(ev.view) %d vno',
    'set menu ".view${vno}.mbar.view.m.show"',
    'if {![winfo exists $menu]} {error "missing ABRAXAS menu"}',
    'set before [expr {[info exists View(ABRAXAS)] ? $View(ABRAXAS) : -1}]',
    'if {$before != 1} {$menu invoke 1}',
    'update',
    'update idletasks',
    'set after [expr {[info exists View(ABRAXAS)] ? $View(ABRAXAS) : -1}]',
    'format "before=%s;after=%s;menu=%s;view=%s" $before $after $menu $vno',
]
# NOTE: previously had these lines after 'update idletasks':
#   'catch {UpdateView $View(ev.view)}',
#   'catch {event generate .view${vno}.gl0 <Expose>}',
#   'update',
#   'update idletasks',
# They were removed. The first 'update'/'update idletasks' pair remains.
```

**Impact:** Removes explicit render-forcing (`UpdateView` + `<Expose>`) from the ABRAXAS enablement helper. The single remaining `update`/`update idletasks` pair is kept for menu state propagation.

### 3.2 Test Changes

**File: `tests/test_cmapi_testrun_control.py`**

New/updated tests:
- `TestMovieEventPumpMitigations.test_movie_background_tcl_commands_do_not_flush_event_loop` — asserts no `update`/`update idletasks` in background commands
- `TestMovieAbraxasProbe.test_ensure_movie_abraxas_enabled_avoids_forcing_render` — asserts no `UpdateView`/`<Expose>`/`UpdateView_TimerProc` in ABRAXAS body
- `TestMovieEventPumpMitigations.test_ensure_movie_abraxas_enabled_raises_when_probe_does_not_latch` — behavioral test: simulates `before=0;after=0` and asserts `RuntimeError("IPG-MOVIE ABRAXAS did not stay enabled")`

**File: `tests/test_persistent_counters.py`**

New test:
- `TestMovieFboCaptureScript.test_capture_movie_keeps_pre_fbo_section_free_of_event_pumping` — asserts `_capture_movie_via_dde_fbo()` Tcl body has no `update`/`update idletasks`/`UpdateView`/`<Expose>` before `set captureFBO [FBO new ...]`

### 3.3 What Was NOT Changed

- **`camera_calibration.py`**: `_capture_movie_via_dde_fbo()` Tcl body was already clean before `FBO new` — no changes needed
- **`ensure_movie_view_size()`**: Still contains `update`/`update idletasks` (lines 1748-1749) — left for next round
- **`ensure_movie_camera_selected()`**: Still contains 3 rounds of `update`/`update idletasks` (lines 1838-1848) — left for next round
- **`ensure_movie_camera_widgets()`**: Still contains `update`/`update idletasks` (lines 1920-1931) — left for next round
- **`ensure_movie_camera_dialogs_normal()`**: Still contains 3 rounds of `update`/`update idletasks` (lines 1979-1994) — left for next round

---

## 4. Test Results

### Unit Tests (After 60aa02c)

```
python -m pytest tests/test_cmapi_testrun_control.py tests/test_persistent_counters.py -q
# 25 passed

python -m pytest tests -q
# 31 passed

python -m py_compile cmapi_testrun_control.py tests/test_cmapi_testrun_control.py tests/test_persistent_counters.py
# (no output = success)
```

### Known Blocker for Full Suite

`test_fbo_after_prepare_step.py` (root-level diagnostic script) still calls `ensure_movie_camera_selected("right_rear", timeout_sec=8.0, skip_fbo_probe=True)` but `skip_fbo_probe` parameter no longer exists. This blocks `pytest -q` from root. This is NOT caused by 60aa02c — it was pre-existing.

---

## 5. Runtime Verification Results

### Environment State

Online session checked at 2026-06-04 09:42 UTC:
- `HIL.exe` (PID 35368) — CarMaker Office online
- `Movie.exe` (PID 30912) — IPGMovie online
- `Movie.exe` (PID 35212) — GPUSensor online
- TestRun: `vctc_ngxpro` / `kel` online

### Round 1: Stepwise Prepare → FBO (No camera_selected)

```
STEP baseline          → FBO result_ok (0.92s)
STEP after_abraxas     → action=abraxas_enabled, FBO result_ok (0.40s)
STEP after_view_size   → action=view_size_applied 960x640, FBO result_ok (0.40s)
STEP after_widgets     → action=camera_widgets_ready, FBO result_ok (0.44s)
STEP after_dialogs     → action=camera_dialogs_normal, FBO result_ok (0.42s)
```

Result: **All clean.** No FBO failures after any prepare helper.

Output: `SimOutput\dde_health_check\20260604_094217\runtime_stepwise_fbo_verify\summary_20260604_094221.json`

### Round 2: camera_selected 专项

| Candidate | Selection | FBO After |
|-----------|-----------|-----------|
| `CAMERA_RSI-SENSOR Vhcl.right_rear` | ✅ latched | ✅ result_ok (0.45s) |
| `right_rear` | ✅ latched | ✅ result_ok (0.42s) |
| `CAMERA_RSI-SENSOR Vhcl.rear_tv` | ❌ not latched (actual=right_rear) | N/A |
| `CAMERA_RSI-SENSOR Vhcl.left_tv` | ❌ not latched (actual=right_rear) | N/A |
| `rear_tv` | ❌ not latched (actual=right_rear) | N/A |
| `left_tv` | ❌ not latched (actual=right_rear) | N/A |

Result: **`right_rear` works end-to-end.** Other sensors don't latch in current session (not an FBO issue — a sensor selection issue).

Output: `SimOutput\dde_health_check\20260604_094257\runtime_camera_select_fbo_verify\summary_202604_094301.json`

---

## 6. Key Architecture Facts

### Production Capture Chain

```
evaluate() → capture_movie() → _capture_movie_via_dde_fbo()
                                    ↓
                            render_dde_execute_script(result_path, "IPG-MOVIE", body_lines)
                                    ↓
                            CarMaker RunScript → dde execute TclEval IPG-MOVIE { ... }
                                    ↓
                            IPG-MOVIE Tcl: FBO new → FBO begin → UpdateView → FBO end → gl readpixels
```

**Critical:** The production capture Tcl body (`camera_calibration.py:7898-7920`) is already clean — no `update`/`update idletasks` before `FBO new`.

### Prepare Chain (Pre-Capture)

```
execute_prepare_mode()
  → ensure_movie_abraxas_enabled()    # FIXED: removed UpdateView/Expose
  → ensure_movie_camera_selected()    # STILL HAS: 3x update/update idletasks
  → ensure_movie_view_size()          # STILL HAS: 1x update/update idletasks
  → ensure_movie_camera_widgets()     # STILL HAS: update/update idletasks
  → ensure_movie_camera_dialogs_normal() # STILL HAS: 3x update/update idletasks
```

### Remaining update/update idletasks Locations

| Function | Lines | Count | Risk Level |
|----------|-------|-------|------------|
| `ensure_movie_camera_selected()` | 1838-1848 | 3 rounds | HIGH — most complex, widget interactions |
| `ensure_movie_camera_dialogs_normal()` | 1979-1994 | 3 rounds | MEDIUM — dialog opening |
| `ensure_movie_camera_widgets()` | 1920-1931 | 2 rounds | MEDIUM — widget materialization |
| `ensure_movie_view_size()` | 1748-1749 | 1 round | LOW — simple resize |

### Risk: verify_runtime_chain_baseline.py

`select_movie_camera_sensor_after_scene_ready()` (line 402-460) has the **exact pattern** that causes FBO failure:

```python
# verify_runtime_chain_baseline.py:428-440
'Camera::ShowSettingsDlg',
'update',
'update idletasks',
'Camera::Select $target $vno',
'update',
'update idletasks',
'.camera.btn.set invoke',
'update',
'update idletasks',
'set wi [dict get $View($vno) Width]',
'set he [dict get $View($vno) Height]',
'set captureFBO [FBO new $wi $he -tex rgb -noclear]',  # ← FBO after 3 rounds of update
```

This is a **verification script** (not production), but it confirms the pattern: 3 rounds of `update`/`update idletasks` directly before `FBO new` in the same Tcl execute = high failure risk.

---

## 7. Hypotheses for Next Agent

### H1: `ensure_movie_camera_selected()` is the highest-risk remaining helper

- Contains 3 rounds of `update`/`update idletasks` in a single Tcl execute
- Interacts with Camera Settings dialog (`Camera::ShowSettingsDlg`, `Camera::Select`, `.camera.btn.set invoke`)
- Most complex helper with widget state management
- Runtime verification showed it works for `right_rear`, but with only 1 sample

**Test:** Run `ensure_movie_camera_selected("right_rear")` → minimal FBO, repeat 20 times. If any fail, this is the smoking gun.

### H2: Remaining helpers are safe individually, but cumulative pumping matters

Each helper runs in its own Tcl execute (separate DDE call), so the event pump from one helper finishes before the next starts. The risk is within a single Tcl execute, not across helpers.

**Test:** Run the full prepare chain (all helpers) → capture, repeat 20 times.

### H3: Real capture chain may still have undiscovered pre-FBO pumping

The production capture body is clean, but there may be Python-level code between `prepare_mode` and `capture_movie()` that pumps events.

**Test:** Add logging to `_capture_movie_via_dde_fbo()` to confirm no `update`/`update idletasks` is injected between prepare completion and `FBO new`.

### H4: The bug is probabilistic, not deterministic

Even with the same code path, FBO failure may depend on GL driver internal state, timing, or GPU load. The fix reduces probability but may not eliminate it entirely.

**Test:** Run the full prepare→capture chain 100 times and measure failure rate before/after.

---

## 8. Recommended Next Steps (Priority Order)

1. **Run real capture chain 20x on right_rear**
   - Use `_capture_movie_via_dde_fbo("test")` directly
   - Measure: does any attempt fail with `FBO Creation error`?
   - This is the most important test — proves whether 60aa02c actually fixes the production issue

2. **If failures persist, isolate `ensure_movie_camera_selected()`**
   - Run `ensure_movie_camera_selected("right_rear")` → FBO, repeat 20x
   - If this fails, the next fix is to reduce `update`/`update idletasks` in this helper
   - Be careful: these updates may be needed for widget materialization

3. **If `camera_selected` is clean, try full prepare chain**
   - Run all helpers in order → capture, repeat 20x
   - This tests cumulative effect

4. **If all clean, run 100x endurance test**
   - Full prepare→capture cycle, 100 iterations
   - Measure: failure rate, any patterns in failures

5. **Consider removing remaining `update`/`update idletasks` from other helpers**
   - One at a time, with runtime verification after each
   - Priority: `ensure_movie_camera_selected()` > `ensure_movie_camera_dialogs_normal()` > `ensure_movie_camera_widgets()` > `ensure_movie_view_size()`
   - Risk: widget materialization may need the updates

---

## 9. Files Reference

### Production Code

| File | Path | Key Lines |
|------|------|-----------|
| `camera_calibration.py` | `Data/Script/CameraCalibration/camera_calibration.py` | 7875-7992 (`_capture_movie_via_dde_fbo`), 7994-7995 (`capture_movie`) |
| `cmapi_testrun_control.py` | `Data/Script/CameraCalibration/cmapi_testrun_control.py` | 88-109 (`_movie_background_tcl_commands`), 1700-1770 (`ensure_movie_view_size`), 1773-1813 (`ensure_movie_abraxas_enabled`), 1816-1895 (`ensure_movie_camera_selected`), 1898-1954 (`ensure_movie_camera_widgets`), 1957-2018 (`ensure_movie_camera_dialogs_normal`) |
| `dde_health_check.py` | `Data/Script/CameraCalibration/dde_health_check.py` | 129-180 (`render_dde_execute_script`) |

### Tests

| File | Key Tests |
|------|-----------|
| `tests/test_cmapi_testrun_control.py` | `test_movie_background_tcl_commands_do_not_flush_event_loop`, `test_ensure_movie_abraxas_enabled_avoids_forcing_render`, `test_ensure_movie_abraxas_enabled_raises_when_probe_does_not_latch` |
| `tests/test_persistent_counters.py` | `test_capture_movie_uses_view_dict_dimensions`, `test_capture_movie_keeps_pre_fbo_section_free_of_event_pumping` |

### Diagnostic Scripts (Root Level — Not Part of Test Suite)

| File | Purpose | Status |
|------|---------|--------|
| `test_fbo_after_prepare_step.py` | Step-by-step prepare→FBO diagnostic | BROKEN: uses removed `skip_fbo_probe` param |
| `verify_runtime_chain_baseline.py` | Full runtime chain verification | Works but has the pre-FBO update pattern |
| `fbo_score_check.py` | Standalone FBO capture probe | Works, useful for manual testing |

### Temporary Scripts (E:\Temp\opencode\)

| File | Purpose |
|------|---------|
| `runtime_stepwise_fbo_verify.py` | Stepwise prepare→FBO runtime probe |
| `runtime_camera_select_fbo_verify.py` | Camera selection→FBO runtime probe |
| `fbo_new_inline_update_compare.py` | Controlled experiment: inline update before FBO |
| `fbo_new_update_pressure.py` | Controlled experiment: update intensity/frequency |
| `fbo_new_contention_subsplit.py` | Controlled experiment: background contention patterns |
| `background_probe_spammer_subsplit.py` | Background DDE probe spammer |

### Result Directories

| Directory | Contents |
|-----------|----------|
| `SimOutput\dde_health_check\20260604_094217\runtime_stepwise_fbo_verify\` | Round 1 stepwise prepare→FBO results |
| `SimOutput\dde_health_check\20260604_094257\runtime_camera_select_fbo_verify\` | Round 2 camera selection→FBO results |

---

## 10. Git History

```
60aa02c fix: reduce movie pre-capture event pumping
0bb05ff fix: avoid recursive movie timer update
9e06b95 fix: align staged FBO result paths
64a2c3d test: add staged FBO probe diagnostics
df80680 fix: restore movie FBO size source
```

### Uncommitted Changes (Not Part of 60aa02c)

```
configs/camera.left_tv.json          # config updates
configs/camera.rear_tv.json          # config updates
configs/camera.right_rear.json       # config updates
dde_health_check.py                  # minor additions
```

---

## 11. Key Constraints

1. **Minimal modification baseline**: User requested minimal changes based on commit `79265ef`. All changes should be surgical.
2. **Don't remove `update`/`update idletasks` from widget initialization helpers without testing**: These may be needed for Tk widget materialization. Remove one at a time with runtime verification.
3. **Trust only aligned result files**: Previous timeout/empty success issues were caused by result path mismatches. Always verify result file content, not just existence.
4. **FBO failure → raw immediate retry usually succeeds**: This is a known pattern. The retry mechanism in `_capture_movie_via_dde_fbo()` (6 attempts) handles this. The goal is to reduce the probability of the first failure, not eliminate retries entirely.
5. **Current session only supports `right_rear`**: Other sensors (rear_tv, left_tv) don't latch in the current online session. This is a sensor selection issue, not an FBO issue.

---

## 12. Environment Notes

- **CarMaker version**: win64-14.1
- **Installation**: `D:\IPG\carmaker\win64-14.1`
- **Project root**: `C:\CM_Projects\CMO141_Calibration`
- **Python**: 3.12 (based on `__pycache__` filenames)
- **DDE mechanism**: pywin32 `dde` module → `TclEval` service → `CarMaker` topic → `IPG-MOVIE` target
- **FBO API**: `FBO new $wi $he -tex rgb -noclear` → `FBO begin` → `UpdateView` → `FBO end` → `gl readpixels`
