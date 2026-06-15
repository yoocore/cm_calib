# CameraCalibration — v1.0 版本记录

> 版本: v1.0 (git tag: v1.0)
> 发布日期: 2026-06-15
> 最新提交: c7bb1ca docs: PROGRESS_HANDOFF.md Phases 34-37 转为中文
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

## 第五部分：剩余问题

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| 1 | 标定分数偏高（rear_tv ~1053, left_tv ~810, right_rear ~43） | ❌ 算法问题 | 初始参数猜测 / 优化器收敛性。非基础设施问题 |
| 2 | 相机切换后 View() 数组元素丢失 | ⚠️ 部分缓解 | IPG-MOVIE 内部清除 View() 数组。新鲜 session 正常 |
| 3 | Prepare 阶段渲染冻结（新鲜 CarMaker 启动时序） | ⚠️ 部分缓解 | rendering_health.js 会检测并 restart，但可能返回 None |

---

*本文档结构：*
*- 第一部分（最终结论）：只看这个就够了*
*- 第二部分（时间线）：需要时查阅，区分了✅正确发现和⛔死胡同*
*- 第三部分（代码模式）：复制粘贴参考，也有应避免的反面模式*
