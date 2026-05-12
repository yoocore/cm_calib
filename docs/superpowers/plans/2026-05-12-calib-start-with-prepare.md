# Calib Start — 始终先运行 CM Prepare 再开始标定 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Calib Start 行为改为：预检 → CM Prepare → Prepare 成功后自动启动标定；Calib Stop 支持任意阶段中止。

**Architecture:** 复用现有的 `MainWindow._pending_launch` 跨阶段传递机制，在 `_on_runtime_summary` 中检测 prepare 成功后自动调用 `calibration_service.start()`。Calib Stop 根据当前阶段（preparing/running）终止对应进程。

**Tech Stack:** PySide6 (QProcess), Python 3.12+, 现有 ProcessService/RuntimeService/CalibrationService

---

### Task 1: 修改 main_window.py — 改造 Calib Start 为 预检 → Prepare → 标定 三级流水线

**Files:**
- Modify: `gui_app/main_window.py` (整个文件，约 483 行)
- Test: 见 Task 3（单元测试）

- [ ] **Step 1: 在 `_start_calibration()` 开头插入预检逻辑**

将 `_start_calibration()` 从当前的 "probe → if ready → start" 改为 "precheck → prepare → on success → start"。在调用 precheck 和 prepare 之前插入表单校验。

```python
@Slot()
def _start_calibration(self) -> None:
    try:
        launch = self._build_launch_config()
    except Exception as exc:
        self.calibration_panel.set_failure_summary(str(exc))
        QMessageBox.critical(self, "Start Failed", str(exc))
        return

    # --- 预检 ---
    project_root = Path(self.runtime_panel.project_root_edit.text().strip() or self.project_root)
    if project_root.resolve() != self.precheck_service.project_root:
        self.precheck_service = PrecheckService(project_root)
    precheck_results = self.precheck_service.run_for_cameras(launch.cameras)
    self.calibration_panel.update_precheck_results(precheck_results)
    failed = [r for r in precheck_results if not r.get("ok")]
    if failed:
        self.calibration_panel.set_failure_summary(
            "Precheck failed: " + "; ".join(str(r.get("message", "")) for r in failed)
        )
        QMessageBox.critical(self, "Precheck Failed",
                             "Precheck failed. See the Precheck tree and failure summary for details.")
        return

    # --- Prepare ---
    self.output_panel.log_view.clear()
    self.calibration_panel.clear_failure_summary()
    self._pending_launch = launch
    self._runtime_mode = "prepare"
    camera_sensor = launch.cameras[0] if launch.cameras else None
    self.runtime_service.prepare_runtime(launch.project_root, launch.testrun,
                                         camera_sensor=camera_sensor)
```

- [ ] **Step 2: 修改 `_on_runtime_summary` — prepare 成功后自动启动标定**

在 mode == "prepare" 的 handler 中，当 `_pending_launch` 不为 None 且 status == "ready" 时，自动调用 `calibration_service.start()`：

```python
@Slot(dict)
def _on_runtime_summary(self, payload: dict) -> None:
    self.runtime_panel.set_runtime_summary(payload)
    summary_parts = [
        f"mode={payload.get('mode')}",
        f"status={payload.get('status', payload.get('mode'))}",
    ]
    testrun_control = self._as_text(payload.get("testrun_control"))
    if testrun_control:
        summary_parts.append(f"testrun_control={testrun_control}")
    self.output_panel.append_log(f"[runtime] summary {' '.join(summary_parts)}")
    mode = str(payload.get("mode") or "")
    status = str(payload.get("status") or "")
    if mode == "prepare":
        if status == "ready":
            self.calibration_panel.clear_failure_summary()
        if status == "ready" and self._pending_launch is not None:
            # Auto-start calibration after successful prepare
            launch = self._pending_launch
            self._pending_launch = None
            self.calibration_service.start(launch)
            return
        self._apply_status(AppStatus.READY if status == "ready" else AppStatus.PASSIVE)
    elif mode == "status":
        if self._pending_launch is not None:
            launch = self._pending_launch
            self._pending_launch = None
            if self._is_runtime_ready_for_launch(payload, launch):
                self.calibration_panel.clear_failure_summary()
                self.calibration_service.start(launch)
                return
            self.calibration_panel.set_failure_summary(self._build_runtime_unhealthy_summary(payload, launch))
            self._apply_status(AppStatus.PASSIVE)
        elif status:
            self._apply_status(AppStatus.READY if status == "ready" else AppStatus.PASSIVE)
        else:
            self._sync_control_states()
    else:
        self._sync_control_states()
```

注意：mode=="prepare" 分支的变化：
- 先设置 `clear_failure_summary` / 状态（与之前一致）
- 然后检查 `self._pending_launch`：如果不为 None 且 status=="ready"，则消费 `_pending_launch` 并启动标定，返回
- 否则走原有路径（仅更新状态）

- [ ] **Step 3: 修改 `_stop_calibration()` — 支持 Prepare 阶段中止**

将 `_stop_calibration()` 改为根据当前状态（PREPARING vs RUNNING）终止对应服务：

```python
@Slot()
def _stop_calibration(self) -> None:
    if self.state.status == AppStatus.PREPARING:
        self._pending_launch = None
        self.runtime_service.stop()
    elif self.state.status == AppStatus.RUNNING:
        self.calibration_service.stop()
```

- [ ] **Step 4: 修改 `_sync_control_states()` — Prepare 阶段启用 Stop 按钮**

让 Stop 按钮在 PREPARING 和 RUNNING 状态都可用：

```python
def _sync_control_states(self) -> None:
    runtime_busy = self.runtime_service.is_running
    calibration_running = self.state.status == AppStatus.RUNNING
    preparing = self.state.status == AppStatus.PREPARING
    can_start = self.state.status in {AppStatus.READY, AppStatus.FINISHED, AppStatus.FAILED, AppStatus.STOPPED}

    self.calibration_panel.start_button.setEnabled(can_start and not runtime_busy and not calibration_running)
    self.calibration_panel.stop_button.setEnabled(calibration_running or preparing)
    controls_enabled = not runtime_busy and not calibration_running and not preparing
    self.calibration_panel.precheck_button.setEnabled(controls_enabled)
    self.runtime_panel.probe_button.setEnabled(controls_enabled)
    self.runtime_panel.prepare_button.setEnabled(controls_enabled)
    self.calibration_panel.set_inputs_locked(not controls_enabled)
    self.runtime_panel.set_inputs_locked(not controls_enabled)
```

- [ ] **Step 5: 修改 `_on_runtime_process_finished` — Prepare 成功后清除 `_pending_launch`**

Prepare 成功后（exit_code==0）正常触发 `_on_runtime_summary`（status="ready"），那里已经消费了 `_pending_launch`。但如果 exit_code != 0（prepare 失败），需要清除 `_pending_launch` 并设置失败摘要。当前代码已经有：
```python
if self._runtime_mode == "prepare" and exit_code != 0 and self.state.status == AppStatus.PREPARING:
    self.calibration_panel.set_failure_summary(...)
    self._apply_status(AppStatus.PASSIVE)
```
需要额外添加：
```python
if self._runtime_mode == "prepare" and exit_code != 0:
    self._pending_launch = None
    self._apply_status(AppStatus.PASSIVE)
```
同时保留原有的 `_on_runtime_process_finished` 逻辑。更新后的完整方法：

```python
@Slot(int)
def _on_runtime_process_finished(self, exit_code: int) -> None:
    if self._runtime_mode == "prepare":
        if exit_code != 0:
            self._pending_launch = None
            if self.state.status == AppStatus.PREPARING:
                self.calibration_panel.set_failure_summary(
                    self._build_failure_summary("Prepare failed", self._runtime_recent_lines)
                )
                self._apply_status(AppStatus.PASSIVE)
            else:
                self._sync_control_states()
        else:
            # Prepare succeeded — _on_runtime_summary will handle auto-start if pending_launch is set
            pass
    else:
        self._sync_control_states()
    self._runtime_mode = None
```

- [ ] **Step 6: 更新 `_on_runtime_process_failed` — 清理 `_pending_launch`**

```python
@Slot(str)
def _on_runtime_process_failed(self, error_text: str) -> None:
    self._pending_launch = None
    ...
```

- [ ] **Step 7: 初始化新成员变量**

在 `__init__` 中初始化 `self._pending_launch = None`（已有）。无需新增成员变量，已有 `_runtime_mode` 和 `_pending_launch` 足够。

- [ ] **Step 8: 提交**

```bash
git add gui_app/main_window.py
git commit -m "feat: Calib Start runs precheck then CM Prepare before auto-starting calibration"
```

---

### Task 2: 修改 calibration_panel.py — 状态栏显示 Prepare 阶段信息

**Files:**
- Modify: `gui_app/widgets/calibration_panel.py`

- [ ] **Step 1: 添加 `status_label` 控件用于显示 Prepare 阶段状态**

在 `estimate_label` 下方添加一个 `QLabel` 用于显示当前阶段（Prepare 进行中 / 标定进行中等）：

```python
# 在 __init__ 中，estimate_label 定义后添加：
self.phase_label = QLabel("")
self.phase_label.setStyleSheet("color: #888; font-style: italic;")
```

在 `layout` 中添加（在 `form` 之后）：

```python
layout.addWidget(self.phase_label)
```

- [ ] **Step 2: 添加 `set_phase_label()` 方法**

```python
def set_phase_label(self, text: str) -> None:
    self.phase_label.setText(text)
```

- [ ] **Step 3: 提交**

```bash
git add gui_app/widgets/calibration_panel.py
git commit -m "feat: add phase label to calibration panel for Prepare/Calibrate status"
```

---

### Task 3: 修改 main_window.py — 连接 phase_label 更新与新事件

**Files:**
- Modify: `gui_app/main_window.py`

- [ ] **Step 1: 在 `_on_runtime_process_started` 中更新 phase_label**

当 `_runtime_mode == "prepare"` 时，显示 "CM Prepare 进行中..."：

```python
@Slot()
def _on_runtime_process_started(self) -> None:
    self._runtime_recent_lines.clear()
    self.calibration_panel.clear_failure_summary()
    if self._runtime_mode == "prepare":
        self.output_panel.append_log("[runtime] CM Prepare uses Tcl StartSim/StopSim for the TestRun bootstrap")
        self.calibration_panel.set_phase_label("CM Prepare 进行中...")
        self._apply_status(AppStatus.PREPARING)
    else:
        self._sync_control_states()
```

- [ ] **Step 2: 在 `_on_process_started` 中更新 phase_label**

```python
@Slot()
def _on_process_started(self) -> None:
    self._calibration_recent_lines.clear()
    self.calibration_panel.clear_failure_summary()
    self.calibration_panel.set_phase_label("标定进行中...")
    self._apply_status(AppStatus.RUNNING)
```

- [ ] **Step 3: 在 `_on_runtime_summary` prepare status=="ready" 时更新 phase_label**

```python
if status == "ready" and self._pending_launch is not None:
    launch = self._pending_launch
    self._pending_launch = None
    self.calibration_panel.set_phase_label("CM Prepare 完成，正在启动标定...")
    self.calibration_service.start(launch)
    return
```

- [ ] **Step 4: 在 `_on_process_finished` / `_on_process_failed` 中清除 phase_label**

```python
@Slot(int)
def _on_process_finished(self, exit_code: int) -> None:
    if self.state.status == AppStatus.STOPPED:
        return
    if exit_code != 0:
        self.calibration_panel.set_failure_summary(
            self._build_failure_summary("Calibration failed", self._calibration_recent_lines)
        )
    self.calibration_panel.set_phase_label("")
    self._apply_status(AppStatus.FINISHED if exit_code == 0 else AppStatus.FAILED)

@Slot(str)
def _on_process_failed(self, error_text: str) -> None:
    self.calibration_panel.set_failure_summary(
        self._build_failure_summary("Calibration process error", [error_text, *self._calibration_recent_lines])
    )
    self.calibration_panel.set_phase_label("")
    self._apply_status(AppStatus.FAILED)
    QMessageBox.critical(self, "Process Error", error_text)
```

- [ ] **Step 5: 在 `_on_runtime_process_finished` 中清除 phase_label 的 prepare 状态**

```python
@Slot(int)
def _on_runtime_process_finished(self, exit_code: int) -> None:
    if self._runtime_mode == "prepare":
        if exit_code != 0:
            self._pending_launch = None
            if self.state.status == AppStatus.PREPARING:
                self.calibration_panel.set_failure_summary(
                    self._build_failure_summary("Prepare failed", self._runtime_recent_lines)
                )
                self.calibration_panel.set_phase_label("CM Prepare 失败")
                self._apply_status(AppStatus.PASSIVE)
            else:
                self._sync_control_states()
    else:
        self._sync_control_states()
    self._runtime_mode = None
```

- [ ] **Step 6: 提交**

```bash
git add gui_app/main_window.py gui_app/widgets/calibration_panel.py
git commit -m "feat: show phase label during Prepare and Calibration stages"
```

---

### Task 4: 修改 main_window.py — Calib Stop 在 PREPARING 阶段中止 Prepare 进程并恢复按钮

**Files:**
- Modify: `gui_app/main_window.py`

- [ ] **Step 1: 更新 `_stop_calibration()` 以正确处理 PREPARING 阶段（已完成 Task 1 Step 3）**

已在 Task 1 Step 3 中完成。确认代码如下：

```python
@Slot()
def _stop_calibration(self) -> None:
    if self.state.status == AppStatus.PREPARING:
        self._pending_launch = None
        self.runtime_service.stop()
    elif self.state.status == AppStatus.RUNNING:
        self.calibration_service.stop()
```

- [ ] **Step 2: 在 `_on_runtime_process_finished` 中处理 STOPPED 状态**

当用户主动停止 Prepare 时，`_runtime_mode` 可能为 "prepare" 但 exit_code 为负数（被 terminate）。这时应恢复按钮，清除 phase_label，而不显示错误：

```python
@Slot(int)
def _on_runtime_process_finished(self, exit_code: int) -> None:
    if self._runtime_mode == "prepare":
        if self._pending_launch is None and exit_code != 0:
            # User stopped prepare manually (pending_launch was cleared in stop)
            self.calibration_panel.set_phase_label("")
            self._sync_control_states()
        elif exit_code != 0:
            self._pending_launch = None
            if self.state.status == AppStatus.PREPARING:
                self.calibration_panel.set_failure_summary(
                    self._build_failure_summary("Prepare failed", self._runtime_recent_lines)
                )
                self.calibration_panel.set_phase_label("CM Prepare 失败")
                self._apply_status(AppStatus.PASSIVE)
            else:
                self._sync_control_states()
        # exit_code == 0: prepare succeeded, _on_runtime_summary handles auto-start
    else:
        self._sync_control_states()
    self._runtime_mode = None
```

- [ ] **Step 3: 提交**

```bash
git add gui_app/main_window.py
git commit -m "fix: handle Calib Stop correctly during CM Prepare phase"
```

---

### Task 5: 确认 calibration_orchestrator.py 是否包含内部 Prepare

**Files:**
- Read: `calibration_orchestrator.py`（确认主流程是否在标定前内部调用 Prepare/TestRun 相关逻辑）

- [ ] **Step 1: 检查 orchestrator 主流程**

搜索 `calibration_orchestrator.py` 中是否有以下模式：
- 调用 `cmapi_testrun_control.py`
- 启动/停止 TestRun
- 在标定循环之前执行 Prepare 相关逻辑

- [ ] **Step 2: 结论记录**

若 orchestrator 不含内部 Prepare 步骤：无需修改，且 `calibration_service.start()` 调用的 orchestrator 只做标定，准备阶段由 GUI 负责，不会重复。

若 orchestrator 含内部 Prepare 步骤：向 `calibration_orchestrator.py` 添加 `--skip-prepare` 标记，然后在 `calibration_service.start()` 中根据调用上下文传入该标记。

- [ ] **Step 3: 提交（无论结论如何，记录调研结果）**

```bash
git add calibration_orchestrator.py  # 如有改动
git commit -m "chore: confirm orchestrator prepare behavior after Calib Start refactor"
```

---

### Task 6: 测试验证

**Files:**
- Create: `tests/test_calib_start_flow.py`（放在项目现有的 tests 目录下）

- [ ] **Step 1: 检查现有测试目录结构**

确认 tests 目录位置和测试框架（pytest 或其他）。

```bash
Get-ChildItem -Path . -Recurse -Filter "test_*.py" | Select-Object FullName
```

- [ ] **Step 2: 根据确认的目录结构，编写测试文件**

假设 tests 目录为 `gui_app/tests/`，测试文件为 `gui_app/tests/test_calib_start_flow.py`。

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication

from gui_app.main_window import MainWindow
from gui_app.models.state import AppStatus, CalibrationLaunchConfig


@pytest.fixture
def main_window(qtbot, tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    (project_root / "Data" / "Script" / "CameraCalibration" / "configs").mkdir(parents=True)
    (project_root / "Movie").mkdir()
    win = MainWindow(project_root)
    qtbot.addWidget(win)
    return win


class TestCalibStartFlow:
    """验证 Calib Start 的三级流水线：预检 → Prepare → 标定"""

    def test_start_calibration_precheck_fails(self, main_window, qtbot):
        """预检失败时不应调用 prepare 或 calibration"""
        main_window.precheck_service.run_for_cameras = MagicMock(return_value=[
            {"camera": "cam1", "ok": False, "message": "missing movie file"}
        ])
        main_window.runtime_service.prepare_runtime = MagicMock()
        main_window.calibration_service.start = MagicMock()

        main_window._start_calibration()

        main_window.runtime_service.prepare_runtime.assert_not_called()
        main_window.calibration_service.start.assert_not_called()

    def test_start_calibration_precheck_ok_then_prepare(self, main_window, qtbot, tmp_path):
        """预检通过后应调用 prepare_runtime"""
        main_window.precheck_service.run_for_cameras = MagicMock(return_value=[
            {"camera": "cam1", "ok": True, "message": "ok"}
        ])
        main_window.runtime_service.prepare_runtime = MagicMock()

        main_window._start_calibration()

        main_window.runtime_service.prepare_runtime.assert_called_once()
        args, kwargs = main_window.runtime_service.prepare_runtime.call_args
        assert kwargs.get("testrun") == "vctc_ngxpro"  # default value from RuntimePanel

    def test_runtime_summary_prepare_ready_triggers_calibration_start(self, main_window):
        """prepare 成功后 _on_runtime_summary 收到 status=ready + _pending_launch 应自动启动标定"""
        main_window.calibration_service.start = MagicMock()
        main_window._pending_launch = CalibrationLaunchConfig(
            project_root=main_window.project_root,
            testrun="vctc_ngxpro",
            cameras=["cam1"],
        )

        main_window._on_runtime_summary({
            "mode": "prepare",
            "status": "ready",
            "vehicle": "TestVehicle",
            "active_sensors": ["cam1"],
            "process_counts": {"carmaker": 1, "gui_movie": 1, "gpusensor_movie": 0},
        })

        main_window.calibration_service.start.assert_called_once()
        assert main_window._pending_launch is None  # consumed

    def test_stop_calibration_during_preparing(self, main_window):
        """在 PREPARING 阶段点击 Stop 应停止 runtime_service"""
        main_window.state.status = AppStatus.PREPARING
        main_window._pending_launch = MagicMock()
        main_window.runtime_service.stop = MagicMock()
        main_window.calibration_service.stop = MagicMock()

        main_window._stop_calibration()

        main_window.runtime_service.stop.assert_called_once()
        main_window.calibration_service.stop.assert_not_called()
        assert main_window._pending_launch is None

    def test_stop_calibration_during_running(self, main_window):
        """在 RUNNING 阶段点击 Stop 应停止 calibration_service"""
        main_window.state.status = AppStatus.RUNNING
        main_window.runtime_service.stop = MagicMock()
        main_window.calibration_service.stop = MagicMock()

        main_window._stop_calibration()

        main_window.calibration_service.stop.assert_called_once()
        main_window.runtime_service.stop.assert_not_called()
```

- [ ] **Step 3: 确认 `gui_app/tests/` 目录存在，若不存在则创建**

- [ ] **Step 4: 运行测试**

```bash
pytest gui_app/tests/test_calib_start_flow.py -v
Expected: 5 tests PASS or 5 tests FAIL (if main_window.py not yet modified)
```

- [ ] **Step 5: 提交**

```bash
git add gui_app/tests/test_calib_start_flow.py
git commit -m "test: add Calib Start flow tests (precheck → prepare → calibrate)"
```

---

### 自检清单

对照需求文档和大纲检查：

1. **Calib Start 先做预检** → Task 1 Step 1（`_start_calibration` 开头插入 `PrecheckService.run_for_cameras`）
2. **预检通过后运行 CM Prepare** → Task 1 Step 1（调用 `runtime_service.prepare_runtime`）
3. **Prepare 成功后自动启动标定** → Task 1 Step 2（`_on_runtime_summary` 中 `mode=="prepare"`、`status=="ready"`、`_pending_launch` 非空时调用 `calibration_service.start`）
4. **Prepare 失败显示错误、不启动标定** → Task 1 Step 5（`_on_runtime_process_finished` 中 `exit_code != 0` 时清除 `_pending_launch` 并设置 failure_summary）
5. **Calib Stop 支持中止 Prepare 阶段** → Task 4 Step 1（`_stop_calibration` 中 `AppStatus.PREPARING` 时停 `runtime_service`）
6. **按钮状态正确切换** → Task 1 Step 4（`_sync_control_states` 中 Stop 按钮在 PREPARING/RUNNING 时启用；precheck/prepare 按钮在 PREPARING 时禁用）
7. **UI 显示当前阶段信息** → Task 2 + Task 3（`phase_label` 显示 Prepare/Calibrate/失败等文本）
