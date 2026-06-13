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
| GUI 交互操作 | 检查是否有可用的 CLI/DDE 替代接口，若无则问用户 |

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

### FBO 修复模式（完整 5 步防御）

```tcl
# 在 height bump 后，update 之前：
catch {after cancel UpdateView_TimerProc}
catch {rename UpdateView_TimerProc __saved_UpdateView_TimerProc}
proc UpdateView_TimerProc {args} {}
update
# finally: rename __saved_UpdateView_TimerProc UpdateView_TimerProc
```

> `after cancel` 只取消一个定时器实例（tclTimer.c 的 TimerCancelDo break 在首次匹配后），`rename + no-op proc` 才彻底防御。

### CheckViewPort 递归防御

用 `wrap_checkviewport()` + re-entrant guard，不要用 `install_view_sync_trace()`。

### Height Bump 安全模式

```tcl
set cur_h [wm geometry .]
scan $cur_h "%%dx%%d" w h
.geometry delete
.geometry create $w [expr {$h+1}]
# → 然后执行 rename+no-op 防御 + update，再 set 回 $w $h
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
