---
name: cm_calib_autofix
description: >
  专用于 CarMaker CameraCalibration 标定项目的自主修复闭环。
  当用户给出明确的标定问题修复要求（"FBO 报错"、"标定流程卡住了"、"测试挂了"、"修一下这个"）时，
  必须使用此技能：自己执行脚本/测试、捕获错误、分析根因、改代码、再执行验证，直到问题解决或达到上限。
  禁止只改代码等用户手动验证——你有完整的执行能力，自己闭环。
---

# cm_calib_autofix — CarMaker 标定自主修复闭环

## 核心理念

**不要等用户告诉你错误是什么。** 用户说"修这个"之后，你全权负责：
```
执行 → 捕获输出 → 分析根因 → 修改代码 → 重新执行验证 → 循环直到通过
```
`bash` 跑在用户本地机器上，CarMaker/IPG-MOVIE 也在同一台机器上。你能执行任何用户能执行的命令。

---

## 执行能力清单

### ✅ 你可以直接执行

| 命令 | 说明 |
|------|------|
| `python -m pytest tests/ -v` | 全量测试（注意过滤需 DDE 的用例） |
| `python -m pytest tests/test_xxx.py -v -k "filter"` | 指定测试 |
| `python camera_calibration.py --capture` | 标定捕捉（需要 CarMaker 运行中） |
| `python calibration_orchestrator.py` | 标定编排器 |
| `python dde_health_check.py` | DDE 连接健康检查 |
| `python fbo_score_check.py` | FBO 状态检查 |
| `python xxx.py` | 任何 Python 脚本——`bash` 在用户机器上，有完整的运行环境 |

### ⚠️ 可能需要处理的限制

| 限制 | 处理方式 |
|------|----------|
| CarMaker 未运行 | 先检查进程，如有需要启动 CarMaker（`Start-Process` + 可执行路径），启动后等 DDE 就绪 |
| 长时间运行 | 设置合理的 `timeout`（标定脚本可能需要几分钟），用 `timeout` 参数 |
| 需要管理员权限 | 停下来问用户 |
|| GUI 交互操作 | 检查是否有可用的 CLI/DDE 替代接口，若无则直接问用户。**但窗口最小化/恢复操作属于自动化范围**——用 Tcl `wm state` 或 `ctypes` 直接做 |
| 系统目录/敏感路径 | 涉及 `C:\Windows\`、`Program Files`、注册表等系统级修改时停下来问用户。**项目目录 `C:\CM_Projects\...` 内的任何操作都直接执行** |

---

## 环境检查（必须在执行任何标定命令前完成）

### ⚠️ 关键陷阱

1. **CarMaker 进程名不是 `Carmaker`**：正确名称是 `CarMaker.win64`（或 `CarMaker`）。用 `Get-Process -Name "CarMaker*"` 检查。
2. **IPG-MOVIE 运行 ≠ 状态正常**：必须检查以下状态：
   - `winfo exists .view0` — 窗口存在
   - `info commands CheckViewPort` — CheckViewPort 命令存在
   - `winfo exists .view0` 返回 1 但 CheckViewPort 不存在 = 状态异常，需要重启 IPG-MOVIE
3. **DDE 连接成功 ≠ 状态正常**：DDE 可能返回 `rc=0` 但 Tcl 执行失败。必须检查结果文件内容。

### 环境检查脚本

每次执行标定前，运行以下检查：

```powershell
# 1. 检查 CarMaker 进程（注意进程名）
Get-Process -Name "CarMaker*" -ErrorAction SilentlyContinue | Select-Object -First 3 Id, ProcessName, StartTime

# 2. 检查 IPG-MOVIE 进程
Get-Process -Name "Movie" -ErrorAction SilentlyContinue | Select-Object -First 3 Id, ProcessName, StartTime

# 3. 检查 DDE 连接和 CheckViewPort 状态
python -c "import sys; sys.path.insert(0, '.'); from dde_health_check import run_check_attempt, default_output_dir, render_dde_execute_script; output_dir = default_output_dir(); output_dir.mkdir(parents=True, exist_ok=True); result_file = str(output_dir / 'env_check.txt').replace('\\', '/'); body = [f'set __fp [open \"{result_file}\" w]', 'puts $__fp \"VIEW0:[winfo exists .view0]\"', 'puts $__fp \"CHECKVP:[info commands CheckViewPort]\"', 'puts $__fp \"CHECKVP_SAVED:[info commands CheckViewPort_saved]\"', 'puts $__fp \"TCL_VERSION:[info tclversion]\"', 'close $__fp']; result = run_check_attempt(name='env_check', service='TclEval', topic='CarMaker', output_dir=output_dir, script_text=render_dde_execute_script(output_dir / 'env_check.txt', 'IPG-MOVIE', body), timeout_sec=10); print('DDE ok:', result.get('ok')); import os; f = result_file; print(open(f).read().strip() if os.path.exists(f) else 'result file not found')"

# 4. 验证结果
# - VIEW0:1 = 窗口存在
# - CHECKVP:非空 = CheckViewPort 存在（正常）
# - CHECKVP:空 = CheckViewPort 不存在（异常，需重启 IPG-MOVIE）
```

### 环境异常处理

| 现象 | 原因 | 处理 |
|------|------|------|
| CarMaker 进程不存在 | 未启动或已崩溃 | 启动 CarMaker（需要路径） |
| IPG-MOVIE 进程不存在 | 未启动 | 通过 DDE 启动或要求用户启动 |
| CheckViewPort 不存在 | 之前操作残留或 IPG-MOVIE 未完全初始化 | 重启 IPG-MOVIE |
| DDE 连接失败 | IPG-MOVIE 未就绪或 DDE 服务未注册 | 等待 5s 后重试，最多 3 次 |
| 渲染状态异常 (UVA/SUV/EXP) | IPG-MOVIE 内部状态问题 | 检查渲染健康状态，必要时重启 |
---

## 知识源

遇到不熟悉的错误时，**先查阅** `project_notes/PROGRESS_HANDOFF.md`。该文件包含 30 个 Phase 的完整诊断历史，是最权威的项目知识库。

快速定位：
- FBO 相关 → Phase 2-7, 14-16, 28
- CheckViewPort 递归 → Phase 17-22, 25-27, 29
- View dict 尺寸残留 → Phase 10-12
- 相机模型重初始化 → Phase 12
- apply 脚本 diff-only → Phase 12
- DDE capture 失败 → Phase 24
- StopUpdateView 渲染冻结 → Phase 30

## 已知未解决问题

| 问题 | 状态 | 描述 |
|------|------|------|
| Phase 30: StopUpdateView (SUV=1) | **待调查** | 标定运行中渲染状态异常（UVA=0 SUV=1 EXP=0），导致截图返回 None。独立于 CheckViewPort 问题 |
| 问题 4: 标定分数偏高 | **算法问题** | right_rear ~43, rear_tv ~1055, left_tv ~811，远超 target <5.0。不是 capture bug，是标定算法/初始参数问题 |

遇到这些问题时，不要重复调查已知原因，直接在已知约束下工作。

---

## 工作流

### 第0步：环境检查（必须！）

**在执行任何标定命令前，必须先运行上面"环境检查脚本"部分的检查命令：**

1. 检查 CarMaker/IPG-MOVIE 进程
2. 通过 DDE 检查 CheckViewPort 状态
3. 确认 VIEW0:1 + CHECKVP:非空

如果检查失败，先解决环境问题再继续。常见问题：
- CarMaker 未运行 → 启动 CarMaker
- IPG-MOVIE 未运行 → 启动 IPG-MOVIE
- CheckViewPort 不存在 → 重启 IPG-MOVIE
- DDE 连接失败 → 等待 5s 后重试

### 第1步：理解需求

用户明确说出要修什么。如果需求模糊，问一个问题澄清。
### 第2步：执行 + 捕获

直接通过 `bash` 运行相关命令。关键要点：

- **stderr 和 stdout 都要捕获**：`2>&1` 重定向
- **保存完整输出**：用 `Tee-Object` 写入文件备用
- **退出的进程恢复目录**：如果脚本改变了工作目录
- **检查返回码**：`$LASTEXITCODE`

```
# 推荐模式
python -m pytest tests/test_persistent_counters.py -v 2>&1 | Tee-Object -FilePath tmp/last_run.log
# 检查 $LASTEXITCODE
```

### 第3步：分析根因

分析输出中的错误信息，判断问题类型。不得在未执行的情况下猜测原因。

**常见标定项目错误模式：**

| 错误特征 | 常见根因方向 |
|-----------|-------------|
| `FBO Creation error` / `FBO error: id not mapped` | GL 上下文不稳定，UpdateView_TimerProc 冲突，height bump 后缺 update |
| `FBO new` 后紧跟 `FBO Creation error` | ConfigFBO 被 UpdateView_TimerProc 触发 |
| `ConnectTo failed` | CarMaker 未运行，或 DDE 服务未注册 |
| `Tcl_Eval` 返回错误 | Tcl 脚本语法错误，或 IPG-MOVIE 未就绪 |
| `after` 脚本相关错误 | Tcl after timer 问题，考虑 rename+no-op 模式 |
| 测试失败（assertion） | 逻辑错误，读取代码定位 |

不确定时，使用 `systematic-debugging` skill 辅助多方向调查。

### 第4步：修复

根据根因修改代码。**一次只修一个问题。** 如果输出包含多个错误，每轮解决一个。

### 第5步：重新执行验证

**必须自己重新执行来验证修复是否有效：**
- 对于测试：重新跑 pytest，确认通过的用例数
- 对于脚本：重新执行并确认不再出现错误
- 如果修复涉及 CarMaker 标定流程，可能需要重跑整个标定

如果重新执行后仍然失败 → 进入下一轮修复循环。
如果成功 → 提交通知，告知用户。

### 第6步：停止条件

满足以下**任一**条件停止：

1. ✅ **修复成功** —— 测试全过 或 用户确认问题解决
2. ⏹ **5 轮未解决** —— 总结尝试过的修复和结果，提供下一步建议
3. ❓ **环境限制** —— 缺少依赖、权限不足、需要特殊硬件，停下来问用户
4. 🤔 **范围外** —— 涉及架构决策或完全不同的模块，咨询用户

---

## 项目特定模式参考
### 项目脚本速查

#### 完整标定管线

```
# 第1步：准备标定环境
python cmapi_testrun_control.py --mode prepare --testrun <Name> --camera-sensor <NAME> --open-movie --keep-carmaker-open --keep-movie-open

# 第2步：单相机标定（prepare 之后运行）
python camera_calibration.py --config configs/camera.<name>.json [--explore-then-refine] [--campaign-rounds N]

# 第2b步：多相机编排（自动完成切换+标定）
python calibration_orchestrator.py --testrun <Name> --camera CAM1 CAM2 ... [--explore-then-refine]

# 查看当前状态
python cmapi_testrun_control.py --mode status

# 只读检查（不启动 CarMaker）
python camera_calibration.py --precheck --camera <NAME>
```

#### 关键参数说明

| 参数 | 用途 |
|------|------|
| `--keep-carmaker-open` | prepare/run 后不关闭 CarMaker，减少后续启动时间 |
| `--keep-movie-open` | prepare/run 后不关闭 IPG-MOVIE |
| `--open-movie` | 启动并附加 IPG-MOVIE GUI |
| `--explore-then-refine` | 先快速多起点探索再细化，适合首次标定 |
| `--multi-start-count N` | N 轮多起点优化（抖动初始参数） |
| `--campaign-rounds N` | 重复 N 轮校准（默认 1） |
| `--print-summary-json` | 输出机器可读的 JSON 摘要行 |
| `--bootstrap-template` | 自定义 bootstrap 模板路径 |
| `--config-dir <path>` | `camera.<name>.json` 配置存放目录（默认 `configs/`） |
| `--testrun-control-mode tcl` | 通过纯 Tcl 引导（默认，更可靠） |

---

### 代码修复关键规则

修复 FBO / CheckViewPort / Height Bump 相关问题时，**必须**先读 `project_notes/PROGRESS_HANDOFF.md` 对应 Phase 获取完整实现细节。以下是核心规则摘要：

| 规则 | 原因 | 详见 |
|------|------|------|
| Height bump 必须用 `__orig_during_bump` 做临时 rename | 与 guard 系统的 `CheckViewPort_saved` 隔离 | Phase 29 |
| `after cancel UpdateView_TimerProc` + `rename + no-op` 必须一起用 | `after cancel` 只取消一个 timer 实例 | Phase 28 |
| CheckViewPort 防御用 `wrap_checkviewport()` + delete-trace | IPG-MOVIE C++ 会重注册 proc，简单 rename 会丢失 | Phase 27 |
| **不要**用 `install_view_sync_trace()` | `View::SetSize` 是 Tcl proc 不是 C++，trace 会丢失 | Phase 27 |
| Height bump 后必须有 `update` 稳定 GL 上下文 | 否则 `UpdateView` 内部 FBO 操作失败 | Phase 28 |
| `update` 前必须 cancel + rename UpdateView_TimerProc | `update` 会触发 30s 定时器内的 ConfigFBO → FBO new | Phase 28 |

### 窗口管理

与 IPG-MOVIE 通信通过 **DDE**（不是 Tk `send`）。具体 DDE 调用模式见上面"环境检查脚本"部分。

**常用 Tcl 命令：** 最小化 `wm state . iconic` / 恢复 `wm state . normal` + `raise .` / 检查 `wm state .`

| 标定阶段 | 窗口状态 | 原因 |
|---------|---------|------|
| prepare | 正常/前台 | 需要 GUI 交互 |
| 优化阶段 | 最小化 | 纯计算，自动使用 FBO 离屏渲染 |
| 切换相机间 | 正常 | Orchestrator 内部自动处理 |

---

### 常见错误速查

遇到以下错误时，先查 `PROGRESS_HANDOFF.md` 对应 Phase，再定位代码。

| 错误特征 | 方向 | Phase |
|---------|------|-------|
| `FBO Creation error` / `FBO error: id not mapped` | GL 上下文不稳定，height bump 后缺 update | 28 |
| `too many nested evaluations` / CheckViewPort 递归 | guard 未安装或被覆盖 | 27, 29 |
| `invalid command name "CheckViewPort"` | rename 命名冲突（`__orig_during_bump`） | 29 |
| `ConnectTo failed` | CarMaker/IPG-MOVIE 未运行 | — |
| `UpdateView_TimerProc call error` | Tcl after 定时器竞争 | 28 |
| `UVA=0 SUV=1 EXP=0` / 截图返回 None | StopUpdateView 渲染冻结（已知未解决） | 30 |
| `APO connect timeout` | SimControlInteractive 连接失败 | — |
| `Scene not ready` | Movie 场景加载超时 | — |
| start_score ≈ final_score 且参数无变化 | 标定系统异常（board 检测/模板不匹配） | — |

---

### 多轮修复示例

修复涉及完整标定流程的问题时，典型循环：

1. **观察**：`cmapi_testrun_control.py --mode status` / `--mode prepare` → 分析输出
2. **标定**：`camera_calibration.py --config ... --explore-then-refine` → 分析结果
3. **回归**：`python -m pytest tests/ -v -k "not dde and not fbo_after_prepare"`

每轮 `2>&1 | Tee-Object -FilePath tmp/<step>.log` 保存完整日志。
---

## 内存管理

每轮修复的关键发现保存到 agentmemory：
- 根因 → `type: bug`
- 修复方案 → `type: architecture`
- 项目约定 → `type: pattern`

---

## 规则

1. **先执行，再分析。** 没有输出就没有发言权。禁止凭空猜测错误原因。
2. **一次只修一个问题。** 多错误输出就逐个修复，每轮一个。
3. **每轮修复必须可验证。** 改完必须自己重新执行确认。
4. **分析先于动手。** 明确根因后再改代码，不要随机尝试。每次关键发现都写入 agentmemory。
5. **诊断优于猜测。** 不确定根因就先加诊断输出再执行，而不是假设原因。

---

## 关键教训记录（从实际运行中累积）

### 教训1：Smoke Test 必须限制迭代次数 + 必须恢复

**场景：** 用户说"冒烟"或"smoke test"，意味着快速验证流程是否能跑通。
**错误做法：** 依赖 config 中的 `max_iters: 180`，让全流程跑完耗时 10+ 分钟。
**正确做法：** 在运行前备份并修改 config 文件，跑完后**必须恢复**。

```bash
# 冒烟模式：备份 → 修改 → 运行 → 恢复
python -c "
import json, shutil
for cam in ['right_rear', 'rear_tv', 'left_tv']:
    p = f'configs/camera.{cam}.json'
    shutil.copy2(p, p + '.bak')          # 备份
    with open(p) as f: cfg = json.load(f)
    cfg['max_iters'] = 5
    with open(p, 'w') as f: json.dump(cfg, f, indent=4)
"
# 运行冒烟测试
python calibration_orchestrator.py --testrun vctc_ngxpro --camera right_rear rear_tv left_tv --campaign-rounds 1

# 恢复原始 config（必须！）
python -c "
import shutil
for cam in ['right_rear', 'rear_tv', 'left_tv']:
    p = f'configs/camera.{cam}.json'
    shutil.move(p + '.bak', p)           # 恢复
    print(f'Restored {p}')
"
```

**关键：** `--campaign-rounds` 不等于冒烟次数。它控制的是整个标定流程重复几遍，不是每个相机的迭代次数。要限制迭代必须改 config。**改完必须恢复，否则后续正式标定会只用 5 次迭代。**

### 教训2：CheckViewPort rename 命名冲突（Phase 29 已修复）

**场景：** Orchestrator 运行标定后报 `invalid command name "CheckViewPort"`。

**根因：** 两个独立系统使用了相同的临时名称 `CheckViewPort_saved`：
1. **Guard 系统**（`wrap_checkviewport()`）：将原始 CheckViewPort 重命名为 `CheckViewPort_saved`，安装 re-entrant guard 作为新 `CheckViewPort`
2. **Height bump**（capture body / ensure_movie_view_size）：也将 CheckViewPort 重命名为 `CheckViewPort_saved`

**冲突过程：**
```
guard: rename CheckViewPort → CheckViewPort_saved (原始), guard 成为 CheckViewPort
height bump: rename CheckViewPort → CheckViewPort_saved → 覆盖了原始！guard 调用 CheckViewPort_saved 时崩溃
```

**修复（commit 12f8aa2）：** Height bump 使用 `__orig_during_bump` 作为临时名称，与 guard 系统的 `CheckViewPort_saved` 隔离。

**规则：** 任何对 CheckViewPort 的临时 rename 操作，**必须**使用 `__orig_during_bump`（不要用 `CheckViewPort_saved`）。

**搜索命令：**
```bash
Select-String -Path tmp/*.log -Pattern "CheckViewPort|invalid command" -SimpleMatch
```

### 教训3：分析必须全面 —— 不要只查 FBO/ConnectTo

**场景：** 检查标定是否导致 CarMaker 报错时，不能只 grep "FBO" 和 "ConnectTo"。
**问题：** CarMaker/IPG-MOVIE 还有其他错误类型（`unknown command`、`invalid command name`、Traceback 等）。
**正确做法：**
- 读取完整日志，不要依赖 grep 摘要
- 检查 orchestration 最终状态 status 字段
- 检查每个相机的 CALIBRATION_SUMMARY_JSON 中的 passed 字段
- 用更宽泛的模式搜索错误

```bash
# 正确检查标定是否失败
$full_log = Get-Content tmp/orchestration.log -Raw
if ($full_log -match '"status":"failed"' -or $full_log -match 'Traceback') {
    Write-Host "FAILED - 需分析完整错误"
}
# 或者
Select-String -Path tmp/orchestration.log -Pattern "(error|Error|ERROR|invalid|unknown|failed|Failed|FAILED|Traceback|Exception)"
```

### 教训4：检测标定是否卡死 —— 参数无变化 = 标定不正常

**场景：** 标定跑了 N 轮但 `start_score == final_score` 且所有参数 start == final。
**判断标准：**
- start_score ≈ final_score（差 < 1e-6）
- 所有参数值 start 和 final 完全相同
- `accepted` 字段持续为 False
- `stop_reason` 是 `max_iters_reached`（从未提前收敛）

这表示标定系统本身有问题（board 检测失败、score 计算不工作、view 配置不正确等），不是参数调整能解决的。应该：
1. 检查 Capture 是否返回有效图像
2. 检查 board 检测结果
3. 检查 template/overlay 是否正确匹配
4. 而不是继续跑更多轮次
