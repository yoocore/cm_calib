# CameraCalibration — Handoff

## 当前状态 (2026-06-14)

### 渲染卡死修复 (最新突破)

**根源**: `IPG-MOVIE` 的 `UpdateView_TimerProc`（Tcl proc, 4998 字节）第10行的守卫：
```tcl
if {($View(StopUpdateView) || $View(UpdateViewActive)) && $Pgm(Exporting)==0} {
    return
}
```
当 `UpdateViewActive == 1` 时守卫触发，函数直接 `return`，不执行结尾的 `set View(UpdateViewActive) 0`（第220行），导致 UVA 永久卡在 1。后续所有 TimerProc 调用在第10行被弹回，渲染完全死亡。

**修复方法**: 恢复 `UpdateViewActive=0`，然后直接调用 `UpdateView_TimerProc`（不先设 UVA=1，让 TimerProc 自己管理状态）。

**验证**: 修复后持续稳定运行 40+ 分钟，`UpdateCounter` 从 29445 增长到 ~45511，76fps。`dde_health_check.py` 全部通过。

### 发现的其他 Bug

1. **`movie_render_probe` 误报**: 原代码检查 `UVA==1` 作为"渲染活跃"标志，但健康渲染帧间 UVA 是 0（`TimerProc` 末尾设为 0），快照采样必然看到 UVA=0 产生误报。修复方法：移除 UVA==1 检查，保持 SUV==0 检查。渲染活跃性验证通过 `rendering_health.py` 的 Python 端双点 UC 增长检测。

2. **`RestartUpdateView` 不足以修复**: 它只 `after $delay UpdateView_TimerProc` 调度一次定时器，没有解决 UVA 卡死的问题。

### 新代码文件

| 文件 | 用途 |
|------|------|
| `rendering_health.py` | `check_render_state()` — 快照检查 UVA/SUV/UC；`try_restart_rendering()` — 检测 + 自动修复渲染卡死，带 Python 端双点 UC 增长验证 |

### DDE 远程控制要点

1. **RunScript 必须用绝对路径**: CarMaker 的 TclEval 环境的工作目录不明，相对路径导致找不到 .tcl 文件。`dde_health_check.py` 使用 `result_path.resolve().as_posix()` 生成路径。

2. **`dde execute TclEval IPG-MOVIE {...}` 中的 `after ms` 不阻塞**: dde execute 上下文中 Tcl 事件循环状态不确定，不能用 `after 200` + `set uc1 $::View(UpdateCounter)` 做双点检测。必须在 Python 端做两次独立 `dde execute` 调用，中间加 `time.sleep()`。

3. **`after ms`（无回调）在 IPG-MOVIE 主循环中阻塞**: 在 TimerProc 内部的 `after [expr {int(110-$View(TimingPeriod))}]` 是阻塞的 (WaitForMultipleObjects)，但通过 dde execute 执行时不阻塞——`after` 调度定时器后立即返回，不等待到期。

4. **永远不要设 UVA=1 再调 TimerProc**: TimerProc 内部逻辑：
   - 第100-130行：读 `::View(TimingPeriod)` 计算延迟
   - 第215-220行：设 UVA=0，设下一次 `after` 回调
   直接设 UVA=1 + call TimerProc 会导致 TimerProc 在第10行立即 return。

### Win32 Capture 实现

- `capture_viewport_win32.py`: 用 `win32gui.PrintWindow` 替代损坏的 `glReadPixels`
- 窗口查找按 `Tk-OGL` 子控件 + width>=640 过滤（排除附件对话框）
- PrintWindow 返回的数据已经是 top-to-bottom，不需要 `transpose(FLIP_TOP_BOTTOM)`
- 输出 960x640 彩色 PNG

### 待办 (当前标定流程)

即将启动三相机标定（orchestrator 模式，`vctc_ngxpro` TestRun），涵盖 rear_tv, left_tv, right_rear。
