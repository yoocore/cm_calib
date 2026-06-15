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
| `python rendering_health.py` | 渲染循环健康检查（UVA/SUV/UC 状态） |
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

1. **CarMaker GUI 进程名是 `HIL`**（不是 `Carmaker`）：完整列表 `HIL.exe`, `CarMaker.win64.exe`, `CM_Office.exe`
2. **IPG-MOVIE 进程名是 `Movie`**：GUI Movie 和 GPUSensor 都叫 `Movie.exe`，通过 CommandLine 区分
3. **IPG-MOVIE 运行 ≠ 状态正常**：必须检查以下状态：
   - `winfo exists .view0` — 窗口存在
   - `info commands CheckViewPort` — CheckViewPort 命令存在
   - `StopUpdateView` — 渲染管线是否停止（SUV=1 表示死锁，需重启）
   - GL widget 尺寸 vs View dict 尺寸是否一致
4. **DDE 连接成功 ≠ 状态正常**：DDE 可能返回 `rc=0` 但 Tcl 执行失败或超时。

### 环境检查脚本

```powershell
# 1. 检查所有 IPG 进程（注意进程名！）
Get-Process -Name "HIL","Movie","CarMaker*","CM_Office" -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, MainWindowTitle, StartTime | Format-Table -AutoSize

# 2. 检查 DDE + 渲染循环健康（UVA/SUV/UC 状态 + 2s 增长验证）
python rendering_health.py
# 期望输出: ok=True uva=0 suv=0 uc=增长中
# 如果 uc 在 2 秒内无增长 → 渲染循环死亡 → 必须重启 CarMaker
```

### 环境异常处理

| 现象 | 处理 |
|------|------|
| HIL 进程不存在 | 启动 CarMaker（见下方重启命令） |
| Movie 进程不存在 | CarMaker 启动后会自动拉起 Movie |
| StopUpdateView=1 (SUV=1) | 渲染死锁，必须重启整个 CarMaker |
| CheckViewPort 不存在 | 重启 IPG-MOVIE（kill Movie 进程即可，CarMaker 会重拉） |
| DDE 超时 | 渲染管线卡死，重启 |

### 重启 CarMaker

```powershell
# Kill 所有（进程名是 HIL + Movie，不是 CarMaker！）
taskkill /IM HIL.exe /F /T 2>$null
taskkill /IM Movie.exe /F /T 2>$null
Start-Sleep -Seconds 3

# 启动（HIL.exe = CarMaker GUI）
Start-Process "D:\IPG\carmaker\win64-14.1\GUI\HIL.exe" -ArgumentList "-projectdir","C:/CM_Projects/CMO141_Calibration" -WorkingDirectory "D:\IPG\carmaker\win64-14.1\GUI"

# 等待就绪（DDE TclEval 可用）
python -c "import sys; sys.path.insert(0,'.'); from cmapi_testrun_control import wait_for_carmaker_tcleval_ready; wait_for_carmaker_tcleval_ready()"
```
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
- 渲染循环死亡 / StopUpdateView → Phase 30-31
- capture_movie() return 缺失 → commit b543d81

## 已知未解决问题

| 问题 | 状态 | 描述 |
|------|------|------|
| Phase 30: 渲染冻结 | **单相机已修复** | 根因：`after cancel` 杀死渲染定时器后未重新调度（commit 47e8d79）。三相机切换仍有问题（Phase 32） |
| Phase 32: 相机切换 View() 丢失 | **未修复** | 相机切换后 View(0) 数组元素不存在，View::SetSize 崩溃。不能用不完整的 dict 初始化（缺 DistortionSrc 等 key） |
| Phase 32: fresh-start DDE 桥接 | **未修复** | 新启动 HIL.exe → IPG-MOVIE 的 `dde execute` 桥接不会立即就绪，`wait_for_movie_scene_ready` 超时。解决：先 `cm prepare` 再 orchestrator + `--skip-prepare-for-first-camera` |
| 问题 4: 标定分数偏高 | **算法问题** | right_rear ~43, rear_tv ~1055, left_tv ~811，远超 target <5.0。不是 capture bug，是标定算法/初始参数问题 |

遇到这些问题时，不要重复调查已知原因，直接在已知约束下工作。

---

## 工作流

### 第0步：环境检查（必须！）

执行上面"环境检查"部分的 3 步检查。任何一项失败则先解决再继续。

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
错误模式速查见下面"常见错误速查"表。不确定时用 `systematic-debugging` skill 辅助多方向调查。

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

# 注意：orchestrator 的 main() 开头会杀所有 CarMaker+Movie 进程重建。
# 如果环境已经 prepare 好、只想复用，加 --skip-prepare-for-first-camera 跳过杀进程。
python calibration_orchestrator.py --testrun <Name> --camera CAM1 ... --skip-prepare-for-first-camera

#### 常用参数（完整列表用 `--help`）

| 参数 | 用途 |
|------|------|
| `--keep-carmaker-open` / `--keep-movie-open` | 不关闭进程，减少后续启动时间 |
| `--explore-then-refine` | 先多起点探索再细化，适合首次标定 |
| `--campaign-rounds N` | 重复 N 轮校准（默认 1） |
| `--print-summary-json` | 输出 JSON 摘要行 |

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
| `dde command failed` (fresh start) | CarMaker→IPG-MOVIE DDE 桥接未就绪 | — |
| orchestrator 杀完所有进程后失败 | `main()` 开头 `kill_existing_cm_processes()` 杀了已 prepare 好的环境。加 `--skip-prepare-for-first-camera` 跳过 | orchestrator main() |

---

### 多轮修复示例

修复涉及完整标定流程的问题时，典型循环：

1. **观察**：`cmapi_testrun_control.py --mode status` / `--mode prepare` → 分析输出
2. **标定**：`camera_calibration.py --config ... --explore-then-refine` → 分析结果
3. **回归**：`python -m pytest tests/ -v -k "not dde and not fbo_after_prepare"`

每轮 `2>&1 | Tee-Object -FilePath tmp/<step>.log` 保存完整日志。

---

### 标定验证清单（每次跑完标定必须检查）

1. **检查输出中无 `[health]` 告警**：`grep "\[health\]" tmp/calib.log`，有告警说明渲染出过问题
2. **检查渲染循环仍活着**：`python rendering_health.py`，UC 应在 2s 内持续增长
3. **检查图像是否真的不同**：对比输出目录下各 iter PNG 的文件大小和 mean/std。全部相同 = 渲染冻结（即使分数略有差异也可能是噪声）
4. **检查分数变化**：如果所有 iter 的 `start_score == final_score` 且参数无变化，说明 capture 返回相同图像（渲染冻结或参数未生效）

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

## 关键教训记录

### 教训1：冒烟测试必须限制迭代 + 必须恢复

改 config 的 `max_iters` 为 5-10 跑冒烟。**改之前 `shutil.copy2(p, p + '.bak')` 备份，跑完必须 `shutil.move(p + '.bak', p)` 恢复。**
`--campaign-rounds` 控制流程重复次数，不等于迭代次数。

### 教训2：CheckViewPort rename 必须用 `__orig_during_bump`

Guard 系统占用 `CheckViewPort_saved`。Height bump 用同名会导致 `invalid command name`。详见 Phase 29。

### 教训3：分析日志要全面

不要只 grep "FBO" 和 "ConnectTo"。用宽泛模式搜索：
```powershell
Select-String -Path tmp/*.log -Pattern "(error|ERROR|invalid|unknown|failed|FAILED|Traceback|Exception)"
```
检查 orchestration 的 `status` 字段和每相机的 `CALIBRATION_SUMMARY_JSON`。

### 教训4：参数无变化 = 标定系统异常

`start_score ≈ final_score` 且所有参数 start == final → 不是参数问题，是 capture/board 检测/模板匹配问题。停止跑更多轮次，检查图像和检测结果。

### 教训5：渲染循环静默死亡——UVA=0 SUV=0 也可能是死锁

**场景：** 标定跑完后 IPG-MOVIE 窗口卡死，但 `check_render_state()` 报告 UVA=0 SUV=0（看起来健康）。
**根因：** `after cancel UpdateView_TimerProc` 取消了渲染定时器。finally 块恢复了 proc 但**没有重新调度定时器**（`after 0 UpdateView_TimerProc`）。渲染循环静默死亡。
**检测方法：**
1. `python rendering_health.py` 检查 UC 是否在 2s 内增长。不增长 = 死锁
2. `capture_movie()` 内置 UC 增长检测：跨迭代比较 UpdateCounter，无增长则触发 restart
3. 标定输出中无 `[health]` 告警 ≠ 正常——如果 UC 检测触发了 restart 会有告警，但如果代码没加 UC 检测则完全静默
**已修复（commit 47e8d79）：** capture body 和 ensure_movie_view_size 的 finally 块后加 `catch {after 0 UpdateView_TimerProc}`。
**验证方法：** 标定完成后运行 `python rendering_health.py`，UC 应在 2s 内持续增长。

### 教训6：三相机切换陷阱——View() 数组丢失 + Prepare 渲染冻结

**场景：** 多相机编排 (right_rear → rear_tv → left_tv) 在第二个相机切换时崩溃。
**两个独立问题：**
1. **View() 数组丢失：** 相机切换后 `View(0)` Tcl 数组元素不存在。`View::SetSize` 内部做 `dict replace` 时崩溃（`can't read "View(0)": no such element in array`）。**不能**用 `[dict create Width W Height H]` 初始化——IPG-MOVIE 需要 DistortionSrc 等更多 key。
2. **Prepare 阶段渲染冻结：** 新鲜 CarMaker 启动后 bootstrap 导致 SUV=1。orchestrator 的 health check 没有 `try_restart_rendering()` 恢复逻辑，直接报错退出。
3. **新鲜启动时序：** 新启动的 CarMaker 上 IPG-MOVIE 初始化需要 >60s，用 `--movie-settle-sec 120` 解决。
**详见 Phase 32。**
