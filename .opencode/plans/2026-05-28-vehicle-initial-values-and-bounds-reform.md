# Vehicle Initial Values & Bounds Reform Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read initial calibration values from the vehicle file (authoritative CarMaker source) instead of config JSON, replace min_offset/max_offset with step×bounds_multiplier auto-bounds, and evaluate both board-matching and board-mismatching history bests to pick the better seed.

**Architecture:** Add `_read_sensor_values_from_vehicle()` to parse the vehicle file on disk. Integrate into main() startup before CameraCalibrator creation. Replace per-parameter min_offset/max_offset with global `bounds_multiplier` (default 30) so bounds = initial ± step×30. Add `_load_history_best_run_any_boards()` and `_evaluate_seed_candidate()` to compare both seed candidates under current boards and pick the better one.

**Tech Stack:** Python 3.12, regex-based vehicle file parsing (existing patterns), CarMaker DDE/Tcl probe for vehicle path resolution.

---

## File Structure

### Files to Modify
- `camera_calibration.py` — main calibration logic (~11942 lines)
  - Add `_read_sensor_values_from_vehicle()` function
  - Add `_read_vehicle_initial_values_via_dde()` helper
  - Add `_load_history_best_run_any_boards()` function
  - Add `_evaluate_seed_candidate()` function
  - Add `_compare_and_pick_better_seed()` function
  - Modify `main()` to read vehicle values before creating CameraCalibrator
  - Modify `_load_params()` to use step×bounds_multiplier when min/max missing
  - Modify `_resolve_parameter_bounds()` to use step×bounds_multiplier
  - Modify `_build_explicit_parameter_config()` to use step×bounds_multiplier
  - Modify `_run_multi_start_rounds()` to compare seed candidates
  - Modify `_run_explore_then_refine_rounds()` to compare seed candidates

- `configs/camera.right_rear.json` — remove min_offset/max_offset from all parameters
- `configs/camera.rear_tv.json` — remove min_offset/max_offset from all parameters
- `configs/camera.left_tv.json` — remove min_offset/max_offset from all parameters

### Files to Test
- `tests/test_camera_calibration.py` — existing test suite (20 tests)

---

## Task 1: Add `_read_sensor_values_from_vehicle()` function

**Files:**
- Modify: `camera_calibration.py` (add function after `_camera_name_matches_vehicle_sensor()` around line 2127)

- [ ] **Step 1: Implement the function**

Add `_read_sensor_values_from_vehicle()` and `_read_vehicle_initial_values_via_dde()` after the existing vehicle helpers.

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_camera_calibration.py -v`
Expected: All 20 tests pass (new function is not yet called)

- [ ] **Step 3: Commit**

```bash
git add camera_calibration.py
git commit -m "feat: add _read_sensor_values_from_vehicle() to read initial values from vehicle file"
```

---

## Task 2: Integrate vehicle initial values into main() startup flow

**Files:**
- Modify: `camera_calibration.py` main() at line ~11823

- [ ] **Step 1: Add vehicle initial value reading before CameraCalibrator creation**

In `main()`, after the config is loaded and before `CameraCalibrator(cfg, config_path=config_path)` at line 11823, add code to:
1. Call `_read_vehicle_initial_values_via_dde(camera_name)`
2. Override `cfg["parameters"][*]["initial"]` with vehicle values

The existing flow becomes:
1. Vehicle file → override `cfg["parameters"][*]["initial"]` (NEW)
2. `_load_params(cfg["parameters"])` → uses vehicle values
3. `capture_initial_values()` → overrides with CarMaker GUI values (existing, takes precedence)

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_camera_calibration.py -v`
Expected: All 20 tests pass

- [ ] **Step 3: Commit**

```bash
git add camera_calibration.py
git commit -m "feat: read initial values from vehicle file via DDE probe before CameraCalibrator init"
```

---

## Task 3: Remove min_offset/max_offset from config files

**Files:**
- Modify: `configs/camera.right_rear.json`
- Modify: `configs/camera.rear_tv.json`
- Modify: `configs/camera.left_tv.json`

- [ ] **Step 1: Remove min_offset/max_offset from all three config files**

For each parameter in the `"parameters"` section, remove `"min_offset"` and `"max_offset"` keys. Keep `"initial"`, `"step"`, `"min_step"`, `"decimals"`.

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_camera_calibration.py -v`
Expected: Tests may fail if they reference min_offset/max_offset — will fix in Task 4

- [ ] **Step 3: Commit**

```bash
git add configs/
git commit -m "refactor: remove min_offset/max_offset from all camera config files"
```

---

## Task 4: Modify code to use step×bounds_multiplier when min/max missing

**Files:**
- Modify: `camera_calibration.py` — `_load_params()`, `_resolve_parameter_bounds()`, `_build_explicit_parameter_config()`

- [ ] **Step 1: Modify `_resolve_parameter_bounds()` to use step×bounds_multiplier**

When neither min_offset/max_offset nor min/max are present, compute bounds as:
`bounds = initial ± step × bounds_multiplier` (default multiplier=30).

- [ ] **Step 2: Modify `_load_params()` to use step×bounds_multiplier**

Same logic: when min/max missing, use `step × bounds_multiplier`.

- [ ] **Step 3: Modify `_build_explicit_parameter_config()` to use step×bounds_multiplier**

Same logic for the explicit config builder.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_camera_calibration.py -v`
Expected: All 20 tests pass

- [ ] **Step 5: Commit**

```bash
git add camera_calibration.py
git commit -m "refactor: use step×bounds_multiplier=30 when min/max bounds missing"
```

---

## Task 5: Evaluate board-matching vs board-mismatching history best, pick better seed

**Files:**
- Modify: `camera_calibration.py` — add `_load_history_best_run_any_boards()`, `_evaluate_seed_candidate()`, `_compare_and_pick_better_seed()`, modify `_run_multi_start_rounds()` and `_run_explore_then_refine_rounds()`

- [ ] **Step 1: Add `_load_history_best_run_any_boards()` function**

Same as `_load_history_best_run_for_config()` but skips the board signature filter. Add after `_load_history_best_run_for_config()` (around line 2032).

- [ ] **Step 2: Add `_evaluate_seed_candidate()` function**

Evaluates a seed candidate by applying its values via DDE and getting the initial score (3-4 seconds).

- [ ] **Step 3: Add `_compare_and_pick_better_seed()` function**

Compares two candidates by evaluating both under current boards, returns the one with lower score.

- [ ] **Step 4: Modify `_run_multi_start_rounds()` to use candidate comparison**

After `_resolve_round_seed_anchor()`, also load the any-board candidate. If both exist, call `_compare_and_pick_better_seed()` to evaluate and pick the better one.

- [ ] **Step 5: Apply same change to `_run_explore_then_refine_rounds()`**

Same candidate comparison logic in the explore-then-refine round function.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_camera_calibration.py -v`
Expected: All 20 tests pass

- [ ] **Step 7: Commit**

```bash
git add camera_calibration.py
git commit -m "feat: evaluate both board-matching and board-mismatching history bests, pick better seed"
```

---

## Task 6: Verify and clean up

**Files:**
- Verify: all modified files

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/test_camera_calibration.py -v`
Expected: All 20 tests pass

- [ ] **Step 2: Check for orphaned code**

Search for any remaining references to removed patterns.

- [ ] **Step 3: Verify config files are valid JSON**

```bash
python -c "import json; json.load(open('configs/camera.right_rear.json')); json.load(open('configs/camera.rear_tv.json')); json.load(open('configs/camera.left_tv.json')); print('All configs valid')"
```

- [ ] **Step 4: Final commit if needed**

```bash
git add -A
git commit -m "chore: clean up after bounds reform"
```

---

## Design Decisions

### 1. Vehicle initial values flow
```
main()
  → _read_vehicle_initial_values_via_dde(camera_name)
    → _probe_runtime_vehicle_context()  [DDE → vehicle path]
    → _read_sensor_values_from_vehicle(vehicle_path, camera_name)  [file parse]
  → override cfg["parameters"][*]["initial"]
  → CameraCalibrator(cfg)  → _load_params() uses vehicle values
  → capture_initial_values()  → overrides with CarMaker GUI values (live state)
```

### 2. Bounds: step × bounds_multiplier=30
When config has no min_offset/max_offset and no min/max:
- bounds = initial ± step × 30
- pos_x (step=0.002): ±0.06m
- pos_y (step=0.001): ±0.03m
- pitch (step=0.02): ±0.6°
- yaw (step=0.01): ±0.3°
- lens_fov (step=0.2): ±6.0°

Bounds shift with each round's new initial (correct behavior).

### 3. Seed candidate comparison
When both board-matching and board-mismatching history bests exist:
1. Evaluate matched candidate → score_a (3-4s)
2. Evaluate any-board candidate → score_b (3-4s)
3. Pick the one with lower score as the seed

This ensures the best possible starting point regardless of board signature mismatch.

### 4. Vehicle writeback preserved
Vehicle file writeback is kept (user confirmed). Config JSON writeback also kept.
