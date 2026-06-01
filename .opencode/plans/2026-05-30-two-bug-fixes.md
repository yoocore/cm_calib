# Two Bug Fixes: Init Score Disappears + "Already Running" During Health Check

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two GUI bugs: (1) init score field showing "-" after final camera run finishes, (2) "A process is already running" error when clicking Start during health check polling.

**Architecture:** Both fixes are in `gui_app/main_window.py`. Bug 1: add a fallback in `_on_orchestration_summary` to the in-progress cached init score. Bug 2: wait for `runtime_service` to complete its health probe before calling `_auto_prepare_and_start`.

**Tech Stack:** Python 3.12, PySide6/Qt

---

### Task 1: Fix init score disappearing after final run

**Root cause:** `_on_orchestration_summary` (main_window.py:1059) uses `calibration.get("start_score")` to populate `CameraResult.init_score`, but the summary payload from `camera_calibration.py` does NOT include `start_score`. Progress events (`camera_run_progress`) DO include `start_score`, and it's cached in `self._camera_progress_init_score[camera_name]`. The summary handler overwrites the correctly-displayed value with `None`.

**Files:**
- Modify: `gui_app/main_window.py:1059`

- [ ] **Step 1: Add fallback to `_on_orchestration_summary`**

Change line 1059 from:
```python
init_score=self._as_float(calibration.get("start_score")),
```
to:
```python
init_score=self._as_float(calibration.get("start_score")) or self._as_float(self._camera_progress_init_score.get(camera_name)),
```

Logic: `self._camera_progress_init_score` stores formatted strings like `"412.8380"` for cameras that received progress events. `_as_float("412.8380")` returns `412.838` (float). If both calibration and cache are None → None → displayed as `"-"`.

- [ ] **Step 2: Verify with py_compile**

```powershell
python -m py_compile gui_app/main_window.py
```
Expected: exit 0, no output.

- [ ] **Step 3: Run tests**

```powershell
pytest tests/ -v
```
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add gui_app/main_window.py
git commit -m "fix: fallback to cached init_score in _on_orchestration_summary when summary payload lacks start_score"
```

---

### Task 2: Fix "already running" when clicking Start during health check polling

**Root cause:** `_start_calibration` → `_auto_prepare_and_start` → `runtime_service.prepare_runtime()` calls `process_service.start_python()` which raises `RuntimeError("A process is already running")` when the health check probe is still active on the same ProcessService.

**Race sequence:**
1. Status=READY → health timer fires → `_check_runtime_health` → `runtime_service.probe_status()` starts QProcess → `_health_check_active=True`
2. User clicks Start → `_start_calibration` → `_is_runtime_ready_for_direct_start` returns False → `_is_runtime_almost_ready` returns True → `_auto_prepare_and_start` → `runtime_service.prepare_runtime()` → `process_service.start_python()` → Raises "already running"

**Fix approach:** In `_start_calibration`, before `_auto_prepare_and_start`, check if `runtime_service.is_running` and wait for the health probe to complete naturally (it's normally < 2 seconds). After waiting, the `_last_runtime_summary` will be updated if the probe succeeded.

**Files:**
- Modify: `gui_app/main_window.py:438` (add wait before `_auto_prepare_and_start`)

- [ ] **Step 1: Add runtime_service wait in `_start_calibration`**

After line 438 (`if self._is_runtime_almost_ready(launch):`), before line 439 (`self._auto_prepare_and_start(launch)`), add:

```python
            if self.runtime_service.is_running:
                self.output_panel.append_log(
                    "runtime_service is busy (health probe?); waiting for completion...",
                    source="system",
                )
                import time
                from PySide6.QtCore import QCoreApplication
                deadline = time.monotonic() + 5.0
                while self.runtime_service.is_running and time.monotonic() < deadline:
                    QCoreApplication.processEvents()
                    time.sleep(0.05)
                if self.runtime_service.is_running:
                    raise RuntimeError("Runtime service did not become idle after 5s timeout")
```

The result (lines 437-440 becomes):
```python
            if not self._is_runtime_ready_for_direct_start(launch):
                if self._is_runtime_almost_ready(launch):
                    if self.runtime_service.is_running:
                        self.output_panel.append_log(
                            "runtime_service is busy (health probe?); waiting for completion...",
                            source="system",
                        )
                        import time
                        from PySide6.QtCore import QCoreApplication
                        deadline = time.monotonic() + 5.0
                        while self.runtime_service.is_running and time.monotonic() < deadline:
                            QCoreApplication.processEvents()
                            time.sleep(0.05)
                        if self.runtime_service.is_running:
                            raise RuntimeError("Runtime service did not become idle after 5s timeout")
                    self._auto_prepare_and_start(launch)
                    return
```

- [ ] **Step 2: Verify with py_compile**

```powershell
python -m py_compile gui_app/main_window.py
```
Expected: exit 0, no output.

- [ ] **Step 3: Run tests**

```powershell
pytest tests/ -v
```
Expected: All 20 tests pass.

- [ ] **Step 4: Commit**

```bash
git add gui_app/main_window.py
git commit -m "fix: wait for runtime_service health probe before _auto_prepare_and_start to prevent 'already running' race"
```
