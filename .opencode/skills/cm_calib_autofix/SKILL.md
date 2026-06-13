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
|| 系统目录/敏感路径 | 涉及 `C:\Windows\`、`Program Files`、注册表等系统级修改时停下来问用户。**项目目录 `C:\CM_Projects\...` 内的任何操作都直接执行** |

---

## 工作流

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
