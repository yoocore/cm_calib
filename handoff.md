# CameraCalibration — Handoff

## 当前状态 (2026-06-14 14:20)

### 渲染卡死修复 (已完成)

**根源**: `IPG-MOVIE` 的 `UpdateView_TimerProc`（Tcl proc, 4998 字节）第10行的守卫：
```tcl
if {($View(StopUpdateView) || $View(UpdateViewActive)) && $Pgm(Exporting)==0} {
    return
}
```
当 `UpdateViewActive == 1` 时守卫触发，函数直接 `return`，不执行结尾的 `set View(UpdateViewActive) 0`（第220行），导致 UVA 永久卡在 1。后续所有 TimerProc 调用在第10行被弹回，渲染完全死亡。

**修复方法**: 恢复 `UpdateViewActive=0`，然后直接调用 `UpdateView_TimerProc`（不先设 UVA=1，让 TimerProc 自己管理状态）。在 `rendering_health.try_restart_rendering()` 中实现。

### 发现的 Bug 汇总

1. **`movie_render_probe` 误报**: 原代码检查 `UVA==1` 作为"渲染活跃"标志，但健康渲染帧间 UVA 是 0（`TimerProc` 末尾设为 0），快照采样必然看到 UVA=0 产生误报。修复：移除 UVA==1 检查，保持 SUV==0 检查。

2. **`RestartUpdateView` 不足以修复**: 它只 `after $delay UpdateView_TimerProc` 调度一次定时器，没有解决 UVA 卡死的问题。

3. **标定迭代循环无渲染健康检测**: `_optimize_coordinate_descent_impl()` 的循环内调用 `evaluate()` → `capture_movie()` → `_capture_movie_via_dde()` 没有任何渲染健康检查。`rendering_health.try_restart_rendering()` 从未被调用。

4. **捕获图像无新鲜度验证**: `evaluate()` 读图后直接做板子检测和打分，不验证图像内容是否与上一帧不同。渲染卡死时每次捕获返回相同冻结图像，得分恒定不变。

### 修复（本轮提交 49d8a51）

在 `camera_calibration.py` 增加两层防御：

**Layer 1 — 捕获前渲染健康检查** (`capture_movie()`):
- 每次捕获前调用 `rendering_health.check_render_state()`
- 若检测到 UVA=1（冻结），执行 `try_restart_rendering()`

**Layer 2 — 捕获后图像新鲜度验证** (`evaluate()`):
- 计算捕获图像像素的 hash，与上一帧对比
- 若相同，尝试重启渲染 + 重新捕获
- 若重启后仍相同，抛出 RuntimeError 中止本轮试验
- 现有的 `_recover_after_runtime_error()` 处理参数恢复和重试

### DDE 远程控制要点

1. **RunScript 必须用绝对路径**: CarMaker 的 TclEval 环境的工作目录不明，相对路径导致找不到 .tcl 文件。`dde_health_check.py` 使用 `result_path.resolve().as_posix()` 生成路径。

2. **`dde execute TclEval IPG-MOVIE {...}` 中的 `after ms` 不阻塞**: dde execute 上下文中 Tcl 事件循环状态不确定，不能在 dde execute 内部用 `after 200` + `set uc` 做双点检测。必须在 Python 端做两次独立 `dde execute` 调用，中间加 `time.sleep()`。

3. **`after ms`（无回调）在 IPG-MOVIE 主循环中阻塞**: 在 TimerProc 内部的 `after [expr {int(110-$View(TimingPeriod))}]` 是阻塞的 (WaitForMultipleObjects)，但通过 dde execute 执行时不阻塞——`after` 调度定时器后立即返回，不等待到期。

4. **永远不要设 UVA=1 再调 TimerProc**: TimerProc 内部第100-130行读 `::View(TimingPeriod)` 计算延迟，第215-220行设 UVA=0 和下一次 `after` 回调。直接设 UVA=1 + call TimerProc 会导致 TimerProc 在第10行立即 return。

5. **发送复杂 Tcl 到 IPG-MOVIE 必须在 Python 端组装脚本文件**: `dde execute TclEval IPG-MOVIE {代码}` 不支持多行/复杂 Tcl。标准模式是 `render_dde_execute_script()` 生成脚本 → 写文件 → `RunScript` 执行。

### Win32 Capture 实现

- `capture_viewport_win32.py`: 用 `win32gui.PrintWindow` 替代损坏的 `glReadPixels`
- 窗口查找按 `Tk-OGL` 子控件 + width>=640 过滤（排除附件对话框）
- PrintWindow 返回的数据已经是 top-to-bottom，不需要 `transpose(FLIP_TOP_BOTTOM)`
- 输出 960x640 彩色 PNG

### 代码文件速查

| 文件 | 用途 | 状态 |
|------|------|------|
| `rendering_health.py` | `check_render_state()` — 快照检查 UVA/SUV/UC；`try_restart_rendering()` — 检测 + 自动修复渲染卡死 | 已添加 |
| `camera_calibration.py` | `capture_movie()` 增加 Layer 1；`evaluate()` 增加 Layer 2 | 已修改（49d8a51） |

### 上一轮标定执行结果（中止）

- 2026-06-14 约 10:50 启动 orchestrator
- rear_tv 完成（无详细日志）
- left_tv 达到 ~21 次迭代时渲染卡死，score 恒定 810.73
- 用户中止，触发本次修复 (Layer 1 + 2 防御)

### CheckViewPort 命名冲突修复 (已完成)

**根源**: 两个独立的 Tcl `rename` 系统使用了相同的临时名称 `CheckViewPort_saved`：

1. `wrap_checkviewport()` (persistent guard) — 将原始 CheckViewPort 重命名为 `CheckViewPort_saved`，然后创建 re-entrant 守卫作为新的 `CheckViewPort`
2. `_capture_movie_via_dde()` 和 `ensure_movie_view_size()` — 在 height bump 期间也将 CheckViewPort 重命名为 `CheckViewPort_saved`

**冲突过程**:
```
wrap_checkviewport(): CheckViewPort → CheckViewPort_saved (原始), 创建守卫作为 CheckViewPort
capture script: rename CheckViewPort CheckViewPort_saved → 覆盖了守卫, 丢失原始命令
capture finally: rename CheckViewPort_saved CheckViewPort → 恢复守卫
结果: CheckViewPort_saved 为空, 守卫调用 CheckViewPort_saved 时崩溃
```

**修复方法** (commit `12f8aa2`): 使用 `__orig_during_bump` 作为 height bump 期间的临时名称，避免与守卫系统的命名冲突。

修改文件:
- `camera_calibration.py` — 2 行
- `cmapi_testrun_control.py` — 2 行
- `tests/test_persistent_counters.py` — 3 行 (更新断言)
- `tests/test_cmapi_testrun_control.py` — 2 行 (更新断言)

### 验证结果

| 检查项 | 结果 |
|--------|------|
| CheckViewPort rename 命名冲突 | ✅ 已修复 (使用 `__orig_during_bump`) |
| 单元测试 | ✅ 121/121 通过 |
| 单次捕获测试 | ✅ 成功，无 CheckViewPort 错误 |
| capture-initials | ✅ 成功读取相机参数 |
| 完整标定运行 | ✅ 无 CheckViewPort 错误 |
| 生成的 Tcl 脚本 | ✅ 确认使用 `__orig_during_bump` |

### 新发现的问题 (待修复)

完整标定运行中发现渲染状态异常:
- `UVA=0 SUV=1 EXP=0` — StopUpdateView 激活
- 导致截图返回 None，标定优化失败
- 这是独立的渲染问题，需要单独调查

### 下一步

1. 调查 `SUV=1` (StopUpdateView) 的触发原因
2. 修复渲染状态异常
3. 重新运行完整标定验证
