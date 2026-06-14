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

## 工作流

### 第0步：环境检查（必须！）

**在执行任何标定命令前，必须先运行环境检查：**

```bash
python check_environment.py
```

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

### 代码修复模式

#### FBO 修复模式（完整 5 步防御）

```tcl
# 在 height bump 后，update 之前：
catch {after cancel UpdateView_TimerProc}
catch {rename UpdateView_TimerProc __saved_UpdateView_TimerProc}
proc UpdateView_TimerProc {args} {}
update
# finally: rename __saved_UpdateView_TimerProc UpdateView_TimerProc
```

> `after cancel` 只取消一个定时器实例（tclTimer.c 的 TimerCancelDo break 在首次匹配后），`rename + no-op proc` 才彻底防御。

#### CheckViewPort 递归防御

用 `wrap_checkviewport()` + re-entrant guard，不要用 `install_view_sync_trace()`。

#### Height Bump 安全模式

```tcl
set cur_h [wm geometry .]
scan $cur_h "%%dx%%d" w h
.geometry delete
.geometry create $w [expr {$h+1}]
# → 然后执行 rename+no-op 防御 + update，再 set 回 $w $h
```

### 窗口管理参考

IPG-MOVIE 窗口状态直接影响标定工作流。关键模式：

#### 最小化 IPG-MOVIE（切换到后台/离屏渲染）

当标定进入计算密集阶段（如多起点优化），可以最小化窗口让出桌面：

```tcl
# 通过 DDE TclEval 发送
send IPG-MOVIE {wm state . iconic}
# 或者用 ctypes 直接操作 Windows HWND
```

> 最小化后，IPG-MOVIE 自动切换到 FBO（离屏帧缓冲）渲染路径，不影响 Capture 功能。

#### 恢复 IPG-MOVIE 到前台

```tcl
send IPG-MOVIE {wm state . normal}
send IPG-MOVIE {raise .}
```

#### 窗口置顶管理（防弹窗遮挡）

```tcl
# 降低窗口层级，防止弹出遮挡
send IPG-MOVIE {wm attributes . -topmost 0}
send IPG-MOVIE {wm lower .}
# 同样适用于 camera 和 cammoddlg 对话框
send IPG-MOVIE {wm attributes .camera -topmost 0}
send IPG-MOVIE {wm lower .camera}
```

#### 标定工作流中的窗口策略

| 阶段 | 窗口状态 | 原因 |
|------|---------|------|
| `cmapi_testrun_control.py --mode prepare` | 正常/前台 | 需要 GUI 交互（场景就绪检测、控件打开） |
| `camera_calibration.py` 优化阶段 | 最小化 | 纯计算，不需要 GUI，让出桌面 |
| `camera_calibration.py` Capture | 最小化也可工作 | 自动使用 FBO 离屏渲染 |
| `calibration_orchestrator.py` 切换相机间 | 正常 | Orchestrator 内部自动处理 |
| 长时间多轮标定 | 最小化后台 | 不干扰用户其他工作 |

执行模式：
```
# 前台运行 prepare
python cmapi_testrun_control.py --mode prepare ...
# → prepare 完成后，最小化 IPG-MOVIE
python -c "import sys; sys.path.insert(0,'.'); from dde_health_check import send_tcl; send_tcl('send IPG-MOVIE {wm state . iconic}')"
# → 后台跑标定
python camera_calibration.py --config ... --explore-then-refine --campaign-rounds 3
# → 完成后恢复前台
python -c "import sys; sys.path.insert(0,'.'); from dde_health_check import send_tcl; send_tcl('send IPG-MOVIE {wm state . normal}; send IPG-MOVIE {raise .}')"
```

---

### 项目特定错误模式补充

#### CMAPI 启动问题

| 错误特征 | 根因方向 |
|---------|---------|
| `ConnectTo failed` | CarMaker 未运行或 DDE 服务未注册 |
| `APO connect timeout` | SimControlInteractive 连接失败，重试参数（`--apo-connect-retries`）不足 |
| `Scene not ready` | Movie 场景加载超时，`--movie-settle-sec` 参数需增加 |
| `TestRun status: idle` | TestRun 未进入 running 状态，检查 TestRun Info 路径 |

#### 捕获/标定问题

| 错误特征 | 根因方向 |
|---------|---------|
| `UpdateView_TimerProc` 冲突 | Tcl after timer 竞争，使用 rename+no-op 防御（见 FBO 修复模式） |
| `CheckViewPort` 递归 | IPG-MOVIE 内部触发，确保 `wrap_checkviewport()` 已安装 re-entrant guard |
| Height bump 后 FBO 错误 | `View::SetSize W H+1` 然后在 update 前缺少 rename+no-op 防御 |
| `No image available` | 视图尺寸异常（width=0 或 height=0），检查 `ensure_movie_view_size()` 顺序 |
| Capture 返回空白 | 检查 ABRAXAS 是否开启、相机是否激活、视图尺寸是否正确 |
| `.camera` 对话框状态异常 | 初始处于 `iconic` 时被捕获后未 `wm deiconify`，先保存初始状态再恢复 |

---

### 多轮修复循环——全链路示例

当需要修复一个涉及完整标定流程的问题时：

**第1轮：观察**
```bash
# 1a. 查看当前状态
python cmapi_testrun_control.py --mode status --testrun <Name> 2>&1 | Tee-Object -FilePath tmp/status.log
# 1b. 如果状态不对，尝试 prepare
python cmapi_testrun_control.py --mode prepare --testrun <Name> --camera-sensor <NAME> --open-movie --keep-carmaker-open --keep-movie-open 2>&1 | Tee-Object -FilePath tmp/prepare.log
# 1c. 分析输出中的错误
# 1d. 修复代码 → 重新执行验证
```

**第2轮：运行标定**
```bash
# 最小化 IPG-MOVIE（可选）
python -c "import sys; sys.path.insert(0,'.'); from dde_health_check import send_tcl; send_tcl('send IPG-MOVIE {wm state . iconic}')"
# 运行标定
python camera_calibration.py --config configs/camera.<name>.json --explore-then-refine --print-summary-json 2>&1 | Tee-Object -FilePath tmp/calib.log
# 分析结果 → 修复 → 重新执行
```

**第3轮：回归测试**
```bash
# 跑项目测试（过滤需要 DDE 的用例）
python -m pytest tests/ -v -k "not dde and not fbo_after_prepare" 2>&1 | Tee-Object -FilePath tmp/pytest.log
```
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

### 教训1：Smoke Test 必须限制迭代次数

**场景：** 用户说"冒烟"或"smoke test"，意味着快速验证流程是否能跑通。
**错误做法：** 依赖 config 中的 `max_iters: 180`，让全流程跑完耗时 10+ 分钟。
**正确做法：** 在运行前修改 config 文件，将每相机迭代限制为 5-10 轮。

```bash
# 冒烟模式：手动覆盖 max_iters（orchestrator 没有 --max-iters 参数）
python -c "
import json
for cam in ['right_rear', 'rear_tv', 'left_tv']:
    p = f'configs/camera.{cam}.json'
    with open(p) as f: cfg = json.load(f)
    cfg['max_iters'] = 5
    with open(p, 'w') as f: json.dump(cfg, f, indent=4)
"
# 然后正常运行 orchestrator
python calibration_orchestrator.py --testrun vctc_ngxpro --camera right_rear rear_tv left_tv --campaign-rounds 1
```

**关键：** `--campaign-rounds` 不等于冒烟次数。它控制的是整个标定流程重复几遍，不是每个相机的迭代次数。要限制迭代必须改 config。

### 教训2：CheckViewPort 多相机切换失败

**场景：** Orchestrator 完成第一相机后切换到第二相机时，IPG-MOVIE 报 `unknown command "CheckViewPort"` 并卡死。

**根因：**
1. 第一相机 prepare 时 `disable_checkviewport_recursion()` 将 CheckViewPort 重命名为 CheckViewPort_saved，安装 wrapper
2. 第一相机 finish 时 `restore_checkviewport()` 恢复 CheckViewPort_saved → CheckViewPort
3. 第二相机 prepare 时再次尝试 wrap CheckViewPort，但 MOVIE 正在重建 view widget，CheckViewPort 作为内部 proc 已不复存在
4. `ensure_movie_view_size()` 中 view size 设置脚本尝试调用 CheckViewPort 相关代码 → 抛出 `unknown command` → 整个脚本失败

**本质：** CheckViewPort 不是持久 Tcl proc，它是 IPG-MOVIE 在创建 view widget 时动态生成的。相机切换时 widget 销毁重建，proc 也随之消失。

**修复方向：**
- `ensure_movie_view_size()` 中对 CheckViewPort 的操作应 graceful degrade：如果 CheckViewPort 不存在，创建一个简单的 no-op placeholder 或直接跳过
- 或者 view size 脚本解耦：CheckViewPort guard 和 view size 设置分开执行，避免 guard 失败导致 size 设置也失败

**搜索命令：**
```bash
Select-String -Path tmp/*.log -Pattern "CheckViewPort|unknown command" -SimpleMatch
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
