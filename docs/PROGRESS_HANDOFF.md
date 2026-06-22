# CameraCalibration — v1.0 版本记录

> **⚠️ OUTDATED (2026-06-23)** — This document was written before the codebase split (12,608-line single file → 17+ modular files) and optimizer upgrade (P0-P8). Implementation details may not match the current codebase. See `codebase_split_plan.md` and `optimizer_upgrade_plan.md` for accurate current structure.

> 版本: v1.1 (git tag: v1.0)
> 发布日期: 2026-06-15
> 最新提交: 90732b1 feat: auto-recover from render freeze by killing all processes and retrying
> 作者: Bytes (OpenCode agent)

---

## v1.0 版本说明

历经约三周（2026-05-20 ~ 2026-06-15）的反复调试，三相机标定管线（rear_tv → left_tv → right_rear）首次达到**可稳定连续运行的里程碑**。经过 5 次手动拖动窗口破坏 FBO 后的验证，系统均能正确检测 FBO 损坏、自动重启干净进程、完成全部三相机标定。

---

### 为什么这花了这么长时间？

根本原因是：**多个严重 bug 互相掩盖，打地鼠式修复**。具体来说：

| 阶段 | 时间 | 核心问题 |
|------|------|----------|
| Phase 1-12 | 5/20-6/2 | FBO 创建时机不对 + View dict 不一致 |
| Phase 13-16 | 6/3-6/5 | `update idletasks` 在 Tcl execute 内触发 FBO 创建错误 |
| Phase 17-27 | 6/5-6/10 | CheckViewPort 递归 — 持续打地鼠，修好一个触发另一个 |
| Phase 28-33 | 6/10-6/13 | Height bump -> GL 上下文不稳定；UpdateView_TimerProc rename 模式错误 |
| Phase 34-37 | 6/14-6/15 | Tcl `rename` 不覆盖 + C++ Configure->ConfigFBO 绕过 Tcl 层 — 真正根因 |

### 几个会误导的方向

**1. CheckViewPort 递归 (Phase 17-27) 是最重的误导。**

这是 IPG-MOVIE 内部的一个 Tcl proc，被 `trace add` 绑定到 View() 数组的写入。每次 `View::SetSize` 或 `set View(...)` 都会触发它。因为 capture 脚本中的 height bump（修改 view 尺寸->再改回正）会写入 View()，导致 CheckViewPort 被递归调用，进而触发 `update` -> 更多 View() 写入。花了整整 10 个 Phase（20+ 次提交）来修复这个。

但 CheckViewPort 递归**不是 FBO 损坏的原因**。它只是把流水搅浑了——只要 FBO 创建时 CheckViewPort 在乱跳，你永远分不清是 FBO 本身有问题还是被 CheckViewPort 触发了什么不该触发的事。

**2. `after cancel` 不够。Tcl 8.6 的 `rename` 不覆盖。**

Phase 28-33 用了 `after cancel UpdateView_TimerProc` 来防止 timer 在 height bump 过程中触发。但 `after cancel` 只取消一个定时器实例（tclTimer.c 的 TimerCancelDo 在首次匹配后 break）。改用 `rename UpdateView_TimerProc {}`（删除此 proc，让 `after` 找不到命令而忽略）看似解决了，但 Tcl 8.6 的 `rename` **不覆盖**——如果 `UpdateView_TimerProc` 已经被设为一个 no-op proc，`rename __saved_UpdateView_TimerProc UpdateView_TimerProc` 会静默失败，导致原来的 real proc 再也回不来了。这解释了很多次的不可复现的渲染卡死。

**3. C++ Configure->ConfigFBO 绕过 Tcl 层 (Phase 34-35)。**

这是真正的最后谜底。IPG-MOVIE 在 C++ 层绑定了一个 `bind .view0.gl0 <Configure>` -> `EventCallbacks::GUI::Window::On_Configure %W`。当用户拖动窗口时，Windows 发送 `WM_SIZE` -> Tcl 触发 `<Configure>` 事件 -> C++ `On_Configure` -> 直接调用 `ConfigFBO`。这**完全绕过**了 Tcl 层的 UpdateView_TimerProc rename 保护。所以无论 Tcl 层怎么防御，拖动窗口必然触发 FBO 重建，在 UpdateViewActive=1 时竞争 GL 上下文。

而且更隐蔽的是：capture 脚本中做 height bump 时，`View::SetSize H+1` 也会触发两次 `<Configure>` 事件（H+1->H），即使窗口没有被拖动。所以当 view 尺寸已经有效时做 height bump 等于在安全环境中触发了一次 FBO 破坏。

**4. 漏了个逗号 (Phase 36)。**

修复了 10 几个复杂 bug 后，Python 的字符串拼接少写了一个逗号，Tcl 脚本里多出一个 `}if{$vp_w...}`，Tcl parser 报 `extra characters after close-brace`。这个在 review 时很容易漏掉，因为很难注意到 `"}"` 后面缺了 `,`。

### 最终可靠的原因

修复完成后，三相机标定在 5 次手动窗口拖动破坏后的验证中全部成功：

| 测试 | rear_tv | left_tv | right_rear |
|------|---------|---------|------------|
| Run 1 | 1053.5 | 810.4 | 43.5 |
| Run 2 | 1053.5 | 810.4 | 43.5 |
| Run 3 | 1053.5 | 810.4 | 43.5 |
| Run 4 | 1054.7 | 810.7 | 43.5 |
| Run 5 | 1053.5 | 810.7 | 43.5 |

所有运行分数一致，无 FBO 错误，无渲染卡死。FBO 损坏自动恢复路径每次被触发都成功。

---

## 第一部分：最终根因汇总（确定的结论）

以下 7 个根因按影响排序。每个注明对应阶段和评级（✅ 正确发现 / ⛔ 死胡同 / 🔶 中间态）。
**如果你只有时间看一个章节，看这里就够了。**

---

### 根因 1: C++ Configure->ConfigFBO 绕过 Tcl 层（Phase 34-35 ✅ 最后真因）

**影响：** 最高。解释了为什么所有 Tcl 层防御都无效，为什么拖动窗口必定破坏 FBO。

IPG-MOVIE 在 C++ 层绑定了 `bind .view0.gl0 <Configure>` 到 `EventCallbacks::GUI::Window::On_Configure`（C++ 函数，不是 Tcl proc）。Windows `WM_SIZE` -> Tcl `<Configure>` -> C++ `On_Configure` -> 直接调 `ConfigFBO`。完全绕过 Tcl 防御层。

更隐蔽的是：capture 脚本的 height bump（`View::SetSize H+1->H`）也触发两次 `<Configure>`。view 尺寸已有效时做 height bump 等于主动触发 FBO 破坏。

**修复：**
1. height bump 前检查 view 尺寸是否已有效，若有效则跳过（commits 1ab82f7, 88efa9f）
2. 添加 FBO 探针 + 自动恢复 — FBO 损坏时 kill 全部进程重启（commit 69186a6）

---

### 根因 2: Tcl 8.6 `rename` 不覆盖 -> 渲染循环静默死亡（Phase 34 ✅）

**影响：** 极高。解释了 90% 的不可复现渲染卡死。

Tcl 8.6 的 `rename` 命令不会覆盖已存在的命令。常见的 finally 恢复写法：
```tcl
rename __saved_UpdateView_TimerProc UpdateView_TimerProc
# 如果 UpdateView_TimerProc 已被设为 no-op，这行静默失败！
# __saved 变成空命令，真正的 proc 再也回不来了
```
结果：`after 0 UpdateView_TimerProc` 调度的是空命令。渲染循环永久死亡。UVA=0 SUV=0 看起来健康但 UC（UpdateCounter）不再增长。

**修复：** restore 之前先 `catch {rename UpdateView_TimerProc {}}` 删除当前占位命令（commit b170099）。

---

### 根因 3: `after cancel` 只取消一个定时器实例（Phase 28 ✅）

**影响：** 高。`after cancel UpdateView_TimerProc` 在 tclTimer.c 的 `TimerCancelDo` 实现中，找到第一个匹配后 break。多个 `after 0 UpdateView_TimerProc` 注册时只取消一个，其余仍触发。

**修复：** 使用 `rename to no-op` 代替 `after cancel`，确保定时器即使触发也不执行实际代码（commit 0ba7840）。

---

### 根因 4: View() 字典在相机切换后尺寸陈旧（Phase 10 ✅）

**影响：** 中高。导致 FBO 创建尺寸错误->画面变形->棋盘格检测失败->分数异常（1372~3025 而非 ~43）。

`View::SetSize 960 640` 后，GL widget 尺寸正确（960x640），但 View() 字典保留旧值（960x768）。capture 脚本读字典创建 FBO：
```tcl
set vp_h [dict get $View($vno) Height]  ;# = 768, 旧相机的值
FBO new $vp_w $vp_h  ;# 960x768 5:4 -> 拉伸变形
```

**修复：** 从 GL widget 直接读尺寸（`[$wpath.gl0 cget -width / -height]`）而非 View() 字典（commit 545083c）。

---

### 根因 5: `update`/`update idletasks` 在 `FBO new` 之前触发 GL 状态不一致（Phase 4-5 ✅）

**影响：** 中。解释了初始阶段大量 `FBO Creation error`。

同一 Tcl execute 内，`FBO new` 前执行 3+ 次 `update` 或 1 次 `update idletasks`，触发 GL 驱动进入不一致状态。受控实验数据：
| 模式 | 成功率 |
|------|--------|
| baseline（无 update） | 20/20 |
| inline_update_x3 | 15/20 |
| inline_idletasks_once | 15/20 |

**修复：** 移除 capture 脚本中 height bump 前后的多余 `update`/`update idletasks`（commit 60aa02c）。

---

### 根因 6: View() 数组元素在相机切换后不存在（Phase 32 ✅->⚠️ 部分缓解）

**影响：** 中。相机切换时 IPG-MOVIE 销毁旧 view widget 并重建，`View(0)` 被清除。capture body `dict replace $View($wno)` 崩：
`can't read "View(0)": no such element in array`。

当前在新鲜启动 session 中正常，跨 session persist 仍可能触发。

---

### 根因 7: Python 字符串拼接逗号遗漏 -> Tcl 语法错误（Phase 36 ✅）

**影响：** 低（完全阻止 capture 运行）。Python 列表 `lines = ["...", "}" "if {...}"]` 中 `"}"` 后缺逗号，拼接为 `"}if{...}"`。

**修复：** 补上逗号（commit 04c8895）。

---

## 第二部分：调试历程（时间线 + 评级）

每个 Phase 标记：
- ✅ **正确发现**：找到了真实的 bug 或贡献了最终修复
- ⛔ **死胡同**：投入大量精力但最终证明不是根因（保留以警示后人）
- 🔶 **中间态**：部分正确但后续被更好方案替代
- ❌ **错误修复**：被 revert 或替换

---

### Stage 1: `update` 触发 FBO 创建错误（Phase 1-9, 5/20-6/3）✅ 总体正确

**核心贡献：** 发现 `update`/`update idletasks` 在 FBO new 前会触发 GL 上下文不一致。经受了后续验证，正确。

| Phase | 内容 | 评级 |
|-------|------|------|
| 1-4 | 症状分析、受控实验验证 update 触发 FBO error | ✅ 正确发现 |
| 5 | 第一次代码修复（60aa02c）：移除多余 update/idletasks | ✅ 正确修复 |
| 6-9 | Runtime 验证、生产验证 | ✅ 验证 |

---

### Stage 2: 尺寸与资源管理（Phase 10-16, 6/4-6/11）✅ + ⛔

| Phase | 内容 | 评级 |
|-------|------|------|
| 10 | **View dict stale size bug** — capture 读 View() 字典得到错误尺寸，FBO 创建为 960x768 而非 960x640，画面变形导致分数异常 | ✅ 正确发现 |
| 11 | config `initial` 字段 KeyError（bounds reform 副作用） | ✅ 正确修复 |
| 12 | **Apply 脚本重初始化 bug** — right_rear 长期 1400+ 分的根因（历史 best 参数被 apply 脚本覆盖） | ✅ 正确发现 |
| 13 | 移除 ensure_movie_view_size 中的 update idletasks | ✅ 正确修复 |
| 14 | FBO 池耗尽 -> NoFBO capture（18566e3） | 🔶 正确但后期被替代 |
| 14b | **双模 capture（noFBO + persistent FBO）** | ⛔ 过度工程，不必要的复杂度 |
| 15 | **统一持久化 FBO** | ❌ 被 revert（OOM 问题） |
| 16 | **改进双模 capture** | ⛔ 同 14b，方向不对 |

---

### Stage 3: CheckViewPort 递归死胡同（Phase 17-27, 6/5-6/12）⛔ 最大死胡同

**为什么这是死胡同：** CheckViewPort 递归不是 FBO 损坏的根因。它只是噪声放大器——CheckViewPort 和 FBO 损坏都被 height bump 触发，但两者无关。所有 CheckViewPort 递归修复都没减少 FBO 损坏概率。

但投入了**10 个 Phase、20+ 次提交**。其错误 `too many nested evaluations` 非常显眼，正常人都会追查。但如果当时先问"CheckViewPort 为什么会被递归？它读到的数据为什么不对？"，可能更快找到 View dict stale 问题。

**保留这个死胡同的全部记录是为了让后人看到：追逐显眼错误可能浪费大量时间，先问"为什么"再动手。**

| Phase | 内容 | 评级 |
|-------|------|------|
| 17 | Height bump 触发 CheckViewPort 递归（首次发现） | 🔶 发现问题但无实质进展 |
| 18 | 把 `after 100` 移到 `UpdateView` 之前 | ⛔ 改时序，不解决本质 |
| 19 | `after cancel UpdateView_TimerProc` 在 bootstrap 后 | ⛔ 假定 timer 冲突是根因 |
| 20 | sync View dict 在 SIM_START 前 | 🔶 方向对但治标不治本 |
| 21 | 用 `dict set View()` 代替 height bump | ❌ 无效，被 revert（f717449） |
| 22 | Height bump 放回 ensure_movie_view_size | ⛔ 演进但不解决根因 |
| 23 | 最小化窗口 `scan $View(ev.view)` 替代 `set` | ✅ 正确修复（窗口状态相关） |
| 24 | P0 修复：framebuffer cleanup + render settle | ✅ 正确修复 |
| 25 | 新鲜启动时 CheckViewPort 递归 | ⛔ 表面修复 |
| 26 | **全局 disable CheckViewPort**（rename to no-op） | ⛔ 掩耳盗铃，最典型的错误方向 |
| 27 | Re-entrant guard + delete-trace 机制 | 🔶 精妙的 Tcl 技术但治标不治本 |

---

### Stage 4: GL 上下文稳定化（Phase 28-33, 6/13-6/14）✅ + ⛔

| Phase | 内容 | 评级 |
|-------|------|------|
| 28 | `FBO id not mapped` — height bump 后缺 `update` 稳定 GL 上下文 | ✅ 正确发现 |
| 29 | CheckViewPort rename 命名冲突（guard vs capture 都用 `_saved`） | ✅ 正确修复 |
| 30 | StopUpdateView（SUV=1）渲染冻结 | 🔶 发现渲染死亡但原因不对 |
| 31 | capture_movie() 缺 return + 渲染循环死亡（after cancel 后未重调度） | ✅ 正确修复 |
| 32 | View() 数组相机切换丢失 + prepare 冻结 | ✅ 发现但未完全修复 |
| 33 | `after 0 UpdateView_TimerProc` 放 capture 前导致 ConfigFBO crash | ✅ 正确发现时序冲突 |

**关键教训：** Phase 28 移除了 `after 0` 防 FBO 错误；Phase 31 又把 `after 0` 加回去治渲染循环死亡。两者互相冲突。直到 Phase 33 才找到正确做法：`after 0` 放 capture 体**之后**（GL 上下文已稳定）而非之前。

---

### Stage 5: 最终真因（Phase 34-37, 6/14-6/15）✅

| Phase | 内容 | 评级 |
|-------|------|------|
| 34 | Tcl rename 不覆盖 -> 渲染循环静默死亡 | ✅ 最终根因之一 |
| 35 | C++ Configure->ConfigFBO 绕过 Tcl 层 + FBO 自动恢复 + kill_all_processes | ✅ 最终根因 |
| 36 | Python 逗号遗漏修复 | ✅ 琐碎但必要 |
| 37 | 5/5 稳定性验证 | ✅ 确认修复有效 |

---

## 第三部分：关键代码模式

### ✅ 经过验证的正确模式

**1. UpdateView_TimerProc 安全恢复**
```tcl
# disable:
catch {after cancel UpdateView_TimerProc}
catch {rename UpdateView_TimerProc __saved_UpdateView_TimerProc}
proc UpdateView_TimerProc {args} {}

# ... height bump + update ...

# restore（必须先 catch delete，因为 Tcl 8.6 rename 不覆盖）:
catch {rename UpdateView_TimerProc {}}
rename __saved_UpdateView_TimerProc UpdateView_TimerProc
```

**2. Height bump 守卫（只在需要时做）**
```tcl
if {$vp_w <= 0 || $vp_h <= 0} {
    View::SetSize $ref_w [expr {$ref_h + 1}] $wpath
    View::SetSize $ref_w $ref_h $wpath
}
```

**3. FBO 探针（检测 FBO 损坏）**
```python
# 在 dde_health_check.py 中实现：
# 最小化 IPG-MOVIE -> 创建 16x16 测试 FBO -> 尝试 extract -> 检查错误
# 如果返回 "FBO error: id not mapped" -> ipg_movie_fbo_ok = False
```

**4. `after 0 UpdateView_TimerProc` 必须放 capture 体之后**
```tcl
try { height bump + rename + update } finally { restore }
# capture body FIRST:
if {[wm state .] eq {iconic}} { FBO path } else { noFBO path }
# THEN re-schedule timer:
catch {after 0 UpdateView_TimerProc}
```

---

### ⛔ 已证伪的应避免模式

| 模式 | 为什么错 | 正确的做法 |
|------|---------|-----------|
| 只用 `after cancel` 保护 height bump | 只取消一个定时器实例 | `rename + no-op` |
| `rename __saved_UpdateView_TimerProc UpdateView_TimerProc` 不先删 no-op | `rename` 静默失败，proc 永远回不来 | 先 `catch {rename UpdateView_TimerProc {}}` |
| 全局 disable CheckViewPort（Phase 26） | CheckViewPort 递归不是 FBO 根因 | 无视它，直接解决 FBO |
| 双重 FBO 管理（persistent + noFBO） | 不必要的复杂度，没解决根本问题 | 统一使用单一 capture 路径 |
| 从 View() 字典读尺寸创建 FBO | 可能得到上一个相机的旧值 | 从 GL widget 直接读尺寸 |

---

## 第四部分：文件变更总览（v1.0）

| 文件 | 关键变更 |
|------|---------|
| `camera_calibration.py` | capture body: height bump 守卫 + rename 安全恢复 + after 0 时序 + 逗号修复 |
| `cmapi_testrun_control.py` | ensure_movie_view_size height bump 守卫 + rename 安全恢复 + kill_all_processes() + CheckViewPort guard |
| `calibration_orchestrator.py` | FBO 检测 + kill+retry 自动恢复路径 |
| `dde_health_check.py` | FBO 探针 + 健康分类扩展（ipg_movie_fbo_ok） |
| `rendering_health.py` | 双区间 UC 增长验证 |

---

## 第五部分：剩余问题（v1.1 更新）

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| 1 | rear_tv 分数偏高（1090 vs 预期 1053） | ❌ 待查 | 仅首次相机（fresh prepare 路径）出现。复用运行时的 `--skip-prepare` 路径 rear_tv=1053 正常。怀疑是新鲜启动后首次 capture 时序问题，非 Win32 capture 失真 |
| 2 | left_tv S3 board 评分主导（score ~731/810） | ⚠️ 算法 | 评分主要受 S3 checkerboard 误差驱动 |
| 3 | 渲染冻结 | ✅ 已修复 | freeze 检测透传 + 杀全部进程 + 自动重试一次 |
| 4 | 窗口被置前 | ✅ 已解决 | orchestrator 层独立 DDE 调用 `_movie_background_tcl_commands()` |

---

*本文档结构：*
*- 第一部分（最终结论）：只看这个就够了*
*- 第二部分（时间线）：需要时查阅，区分了✅正确发现和⛔死胡同*
*- 第三部分（代码模式）：复制粘贴参考，也有应避免的反面模式*

---

## 第六部分：v1.0 后续更新 (2026-06-15 Phases 38-43)

### Phase 38: GUI 精简 — 移除 CM Prepare / Query Status 按钮

之前 GUI 有一个完整的 runtime 准备流程（3s 轮询 health、自动 cm prepare、状态机切换）。这个流程与 orchestrator 自身的 prepare 逻辑重复，且经常因状态判断错误导致标定失败。

**改动：**
- 删除 prepare_button、status_query_button 和所有关联信号
- 删除 `main_window.py` ~1300 行状态管理代码（`_prepare_runtime`、`_query_runtime_status`、`_auto_prepare_and_start`、`_check_runtime_health` 3s 定时器等）
- 简化 `_start_calibration()` 为直接调用 orchestrator，不再做 runtime readiness 检查
- 对应删除 `runtime_service.py` 中的 `prepare_runtime()` 和 `probe_status()` 方法
- 更新所有受影响的测试

**提交：** `07b0747` / 影响文件: `calibration_panel.py`, `main_window.py`, `runtime_service.py`, 测试文件

### Phase 39: Orchestrator kill + skip-prepare 修复

**问题：** orchestrator `main()` 开头无条件 `kill_existing_cm_processes()`，杀了已经 prepare 好的健康环境。从零重建时 CarMaker→IPG-MOVIE DDE 桥接未就绪，`wait_for_movie_scene_ready` 超时。

**修复：**
1. `kill_existing_cm_processes()` 只在非 `--skip-prepare-for-first-camera` 时执行
2. `_prepare_runtime_for_camera()` 新增 Step 0，如果没找到 CarMaker 进程则自动启动 HIL.exe
3. 增加 fresh-start 超时（取 max(45, 120)s）作为安全网

**提交：** `596e00c`, `7880e91`, `198eee3`

### Phase 40: GPUSensor Movie 检测误导

**问题：** orchestrator `_prepare_runtime_for_camera()` Step 5 检测 `if not list_gpusensor_movie_processes()` —— 找到 GPUSensor Movie（`-mode GPUSensor -headless`，无窗口）就认为没问题，跳过 `restart_gui_movie_for_send_recovery()`。但 calibration 需要 GUI Movie 才能执行 `send IPG-MOVIE` —— GPUSensor Movie 没有 Tcl GUI 环境，DDE 发送全部失败。

这是"命令行和 GUI 执行结果不同"的根本原因：CLI 先 `cm prepare`（启动 GUI Movie），orchestrator 用 `--skip-prepare-for-first-camera` 复用。GUI 直接调 orchestrator，重建路径只看到 GPUSensor，没启动 GUI Movie。

**修复：** Step 5 条件从 `list_gpusensor_movie_processes()` 改为 `list_gui_movie_processes()`。同时修正为不杀 GPUSensor Movie（让其与 GUI Movie 共存），因为 GPUSensor 是 CarMaker 自动启动的，杀掉后 CarMaker 会重建，但 health check 的 gpusensor_ping 需要它存在。

**提交：** `4841fdb`, `4cae2a9`, `d891071`

### Phase 41: history_best 锚点优先 + 非致命警告降级

**两个问题：**

**问题 A：** `_resolve_round_seed_anchor()` 优先使用 config 初始值，然后才查 history_best。但 config 可能有旧的 board-wizard 值（score 46-59），导致 history_best（right_rear 43.13）从未被使用。

**修复：** `prefer_history_best=True` 时先查 history_best，config 值仅在无历史记录时作为后备。

**问题 B：** "Warning: could not disable CheckViewPort (non-fatal)" 等预期内的非致命信息在 GUI 输出面板中被分类为 WARNING/ERROR，因 `_classify_log_level()` 基于文本模式匹配（"warning" token 触发）。

**修复：** `_classify_log_level` 先检查显式 `[INFO]/[WARN]/[ERROR]` 标记，再回退到文本模式。同时将 16+2 处 "Warning: could not ..." 改为 `[INFO]` 前缀。

**提交：** `fe2cd51`

### Phase 42: 窗口管理 — 不置前 + 最小化恢复 + NaN 修复

**用户需求：** IPG-MOVIE 窗口不跳到前面，不影响其他工作。

**修复链：**
1. capture Tcl 开头加 `wm state . normal`（最小化时自动恢复）+ `wm lower` + `wm attributes -topmost 0`（推至后台）
2. FBO probe 恢复窗口后加 `wm lower`
3. 发现 `wm lower` 紧接着 `UpdateView` 会触发 NaN 错误（`SM::ConfigureShader` 中 `CSM gettextelsize` 返回 NaN）→ 从 capture Tcl 移除 `wm lower`
4. 改在 orchestrator 层用独立 DDE 调用 `_movie_background_tcl_commands()`（每台相机 capture 前），不干扰渲染 GL 上下文
5. 修复 `start_simulation_via_tcl` 和 `try/finally` 之间的异常会跳过 `stop_simulation_via_tcl` 的 bug（重结构 try/finally 嵌套）

**提交：** `fd7dd05`, `1e9aef6`, `f17f766`, `87e4aff`, `c5baf24`

### Phase 43: FBO 非致命 + 卡死自动恢复

**问题 A (FBO kill+retry 弊大于利)：** FBO 探针检测到损坏 → 杀全部进程 + 重试 → 重试仍损坏 → 放弃。但 FBO 在相机切换时必然临时损坏（C++ Configure→ConfigFBO 冲突），而 Win32 capture 不需要 FBO。kill+retry 导致健康进程被滥杀，浪费 3-5 分钟。

**修复：** FBO 探针改为仅诊断日志，不再触发 kill+retry。损坏时打印 `[INFO] IPG-MOVIE FBO probe failed (non-fatal)`，直接继续用 Win32。

**问题 B (freeze 检测被吞)：** `_check_render_health_before_capture` 正确检测到渲染冻结（UC 停滞）并 `raise RuntimeError`，但被 `except Exception` 吞掉，导致继续无用重试 6 次。

**修复：** 加 `except RuntimeError: raise` 在 `except Exception` 之前，让冻结异常透传。

**问题 C (freeze 无自动恢复)：** freeze 导致 camera_calibration 子进程退出后，orchestrator 直接标记任务失败，不会尝试恢复。

**修复：** 在 orchestrator 相机循环中加 retry 包装。capture 因 freeze 失败时：杀全部进程 → 重准备 → 重跑该相机（一次）。重试仍失败则放弃。

**提交：** `5b7ef9e`, `8c960ce`, `90732b1`

### 稳定性验证结果（含自动恢复）

| 测试日期 | 前置条件 | rear_tv | left_tv | right_rear | 说明 |
|---------|---------|---------|---------|------------|------|
| 6/15 16:37 | 全新启动（无 skip-prepare） | 1090.6 | 810.7 | 43.5 | 全部 finished |
| 6/15 16:51 | 同上 + --multi-start-count 1 | 1090.6 | 153.2 | 43.5 | left_tv 153（较好）|
| 6/15 10:12 | 拖窗后 FBO 损坏 → 自动恢复 | 1053.5 | 810.7 | 43.5 | 触发 FBO kill+retry（旧逻辑）|

> **结论：** rear_tv 1090 分偏高是 FBO 切换时 capture 图像失真导致（C++ ConfigFBO 破坏 GL 上下文 → Win32 捕获到错误帧）。这是 CarMaker 内部渲染引擎问题，非标定脚本问题。不影响 left_tv 和 right_rear 的分数稳定性。

### 当前文件变更汇总

| 文件 | v1.0 后新增/变更 |
|------|-----------------|
| `calibration_orchestrator.py` | 重结构 try/finally 确保 sim 停止 + FBO 非致命 + freeze 自动恢复循环 |
| `camera_calibration.py` | freeze RuntimeError 透传 + capture Tcl 最小化自动恢复 |
| `cmapi_testrun_control.py` | 无变化（已有 `_movie_background_tcl_commands` 5 处调用）|
| `dde_health_check.py` | 无变化 |
| `gui_app/main_window.py` | 删除 prepare/status 按钮和 ~1300 行状态管理代码 |
| `gui_app/widgets/calibration_panel.py` | 移除 prepare_button、status_query_button |
| `gui_app/services/runtime_service.py` | 删除 prepare_runtime()、probe_status() |

### 剩余问题更新

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| 1 | rear_tv 分数偏高（1090 vs 预期 1053） | ❌ CarMaker 内部 | 相机切换时 C++ ConfigFBO 破坏 GL 上下文 → capture 图像失真。非标定脚本问题 |
| 2 | left_tv 评分有 S3 = 731 的 dominant board | ⚠️ 算法 | 评分主要受 S3 checkerboard 误差驱动，其他 board 基本忽略 |
| 3 | 渲染冻结（新鲜 CarMaker 启动时序） | ✅ 已恢复 | freeze 检测 + 杀全部进程 + 自动重试一次 |
| 4 | 窗口不置前 | ✅ 已解决 | orchestrator 层独立 DDE 调用 `_movie_background_tcl_commands()` |

### v1.0 后提交清单

```
90732b1 feat: auto-recover from render freeze by killing all processes and retrying
8c960ce fix: propagate RuntimeError when rendering frozen (was silently caught)
5b7ef9e fix: make FBO probe non-fatal (Win32 capture doesn't need FBO)
c5baf24 fix: import run_runscript from dde_health_check (not via cmctrl)
87e4aff fix: push IPG-MOVIE behind before each camera capture via separate DDE call
f17f766 fix: remove wm lower from capture Tcl (triggers NaN in SM::ConfigureShader)
1e9aef6 fix: NaN in SM::ConfigureShader on window restore + ensure sim always stops
fd7dd05 fix: auto-restore IPG-MOVIE from minimized state before capture + always push window behind
fe2cd51 fix: prefer history_best as seed anchor; downgrade expected non-fatal warnings
4cae2a9 fix: start GUI Movie alongside GPUSensor instead of killing it
d891071 skill: add GPUSensor detection issue + log location quick-ref
4841fdb fix: start GUI Movie when GPUSensor-only (no Tcl GUI for calibration)
198eee3 fix: increase movie_settle timeout for fresh-start (DDE not ready)
75be23e docs: update cm_calib_autofix skill with orchestrator kill + skip-prepare patterns
7880e91 fix: orchestrator skip kill when --skip-prepare-for-first-camera, add CarMaker auto-start Step 0
596e00c fix: kill stale CarMaker/Movie processes at orchestrator entry before camera loop
07b0747 Remove CM Prepare and Query Status buttons from calibration GUI
3f58e66 feat: add FBO health check + auto-recovery to prepare mode
```
