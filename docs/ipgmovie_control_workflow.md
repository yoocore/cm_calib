# IPG-MOVIE 控制流程记录

> **⚠️ OUTDATED (2026-06-23)** — This document was written before the codebase split (12,608-line single file → 17+ modular files) and optimizer upgrade (P0-P8). Implementation details may not match the current codebase. See `codebase_split_plan.md` and `optimizer_upgrade_plan.md` for accurate current structure.

本文档用于沉淀当前工程里已经验证过的 IPG-MOVIE 控制方式、刷新行为、最小化行为，以及后续问答得到的新结论。

当前约定：后续如果围绕 IPG-MOVIE、DDE、Script Control、可见窗口刷新、最小化行为、菜单项控制等继续排查或验证，优先把结论追加到本文档，而不是散落在聊天记录中。

## 1. 当前已验证的主入口

当前可用的后台控制入口是：

1. Python 侧通过 pywin32 DDE 连接 service=`TclEval`、topic=`CarMaker`
2. 执行 `RunScript {C:/.../xxx.tcl}`
3. 在 Tcl 脚本里使用 `send IPG-MOVIE { ... }` 把命令送进 IPG-MOVIE 解释器

这条路径的特点：

1. 不依赖鼠标点击
2. 不依赖键盘输入
3. 不要求 IPG-MOVIE 在前台
4. 窗口最小化时，控制命令仍可生效

当前工程里现成的 DDE 发令实现可参考：

1. [camera_calibration.py](c:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/camera_calibration.py#L3812)
2. [script_control_apply.tcl](c:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/script_control_apply.tcl#L1)
3. [RemoteControlIPGMovie.tcl](c:/CM_Projects/CMO141_Calibration/Data/Script/Examples/RemoteControlIPGMovie.tcl#L1)

## 2. ABRAXAS 控制定位结论

### 2.1 运行时变量

`View -> Show -> ABRAXAS` 对应的运行时变量是：

`View(ABRAXAS)`

已验证：该变量可通过 DDE 在 IPG-MOVIE 进程内直接读写。

### 2.2 菜单项绑定

当前主显示窗口里的真实菜单项位于：

`.view0.mbar.view.m.show`

ABRAXAS 菜单项已验证信息如下：

1. `index=1`
2. `kind=checkbutton`
3. `variable=View(ABRAXAS)`
4. `command=EventCallbacks::Scene::On_Load`
5. `onvalue=1`
6. `offvalue=0`

因此，最稳妥的控制方式不是只写 `View(ABRAXAS)`，而是优先走真实菜单回调：

```tcl
set menu .view0.mbar.view.m.show
set index 1
$menu invoke $index
update
update idletasks
```

这样会尽量复用 IPG-MOVIE 自己的菜单链路，而不是只改一个状态位。

## 3. ABRAXAS 显示链结论

运行时探针已确认：ABRAXAS 不只是一个菜单勾选项，它会进入车辆准备/绘制链。

关键结论如下：

1. IPG-MOVIE 里存在 `CreateObjAbraxas` 和 `CreateObjAbraxas_MC`
2. 在 `PrepareVehicle` / `PrepareVehicle_MC` 中，条件是：

```tcl
if {$View(ABRAXAS) || $FName=="" || [file tail $FName]=="ABRAXAS"} {
    CreateObjAbraxas
}
```

这说明 ABRAXAS 的开关确实会影响渲染内容，不是单纯改变菜单勾选状态。

## 4. 刷新行为结论

### 4.1 只改变量不等于可见窗口一定刷新

实际验证中发现：

1. 仅仅修改 `View(ABRAXAS)`，菜单勾选会变
2. 但可见窗口不一定立刻重绘

原因是主视图的可见重绘链与离屏 FBO 抓图链并不完全等价。

### 4.2 已验证的可见刷新动作

为了让屏幕上可见的 view 尽量刷新，当前测试里使用过这些动作：

```tcl
catch {UpdateView_TimerProc}
catch {event generate .view0.gl0 <Expose>}
update
update idletasks
```

以及：

```tcl
catch {after cancel UpdateView_TimerProc}
RestartUpdateView 0
update
```

### 4.3 当前对刷新链的理解

目前可确认：

1. 离屏渲染内容会变化
2. 可见窗口的重绘链也可以被触发
3. 但在某些时刻，菜单状态改变后，肉眼窗口不一定同步立即变化

因此当前建议是：

1. 控制层优先走菜单 `invoke`
2. 如果用户需要立刻看见变化，再补一层可见视图刷新动作

## 5. 最小化行为结论

### 5.1 最小化时控制仍然有效

已实测：IPG-MOVIE 最小化后，后台 DDE 控制仍然生效。

已验证现象：

1. 最小化状态下可以成功反转 `View(ABRAXAS)`
2. 读回状态会正确变化
3. 该过程不需要把窗口拉回前台

### 5.2 最小化时没有“可见效果”

最小化期间当然不会有肉眼可见的屏幕变化，因为窗口并不显示在桌面上。

更准确地说：

1. 后台控制会生效
2. 状态会改变
3. 窗口恢复显示后，应呈现最终状态
4. 如果恢复后画面没立刻同步，可以再补一次后台刷新

## 6. 当前推荐操作顺序

如果只是切换 ABRAXAS，当前推荐顺序是：

1. 通过 DDE 进入 IPG-MOVIE
2. 走 `.view0.mbar.view.m.show` 的菜单 `invoke`
3. `update` 和 `update idletasks`
4. 如果用户关心屏幕上的即时变化，再补 `UpdateView_TimerProc` / `<Expose>`

推荐 Tcl 片段：

```tcl
set menu .view0.mbar.view.m.show
set index 1
$menu invoke $index
update
update idletasks
catch {UpdateView_TimerProc}
catch {event generate .view0.gl0 <Expose>}
update
update idletasks
```

## 7. 当前已验证的事实摘要

截至 2026-05-08，已验证：

1. ABRAXAS 的运行时变量是 `View(ABRAXAS)`
2. ABRAXAS 的真实菜单项在 `.view0.mbar.view.m.show` 的 `index 1`
3. 菜单回调命令是 `EventCallbacks::Scene::On_Load`
4. 通过菜单 `invoke` 可以在后台切换 ABRAXAS
5. 最小化状态下切换仍然生效
6. 离屏抓图已确认 off/on 的渲染结果不同
7. 可见窗口重绘可通过 `UpdateView_TimerProc` / `<Expose>` 进一步推动

## 8. 后续追加记录

后续如果还有新的 IPG-MOVIE 控制问题、菜单项映射、刷新行为差异、最小化行为验证、其它 Show 项控制方式，都继续追加在这里。

### 8.0.1 2026-05-10 send 故障专题总结入口

围绕本次 `send IPG-MOVIE` / `send GPUSensor_1_0` 坏态、`dde execute` 替代链、证据链、探索过程与最终结论，现已整理成专题文档：

[ipgmovie_send_failure_dde_execute_summary_2026-05-10.md](ipgmovie_send_failure_dde_execute_summary_2026-05-10.md)

如果后续需要快速回答下面这些问题，优先看该专题文档：

1. 当前坏的到底是外层 DDE，还是 Movie-side `send`。
2. 为什么“窗口在线”不等于“send 正常”。
3. 为什么 broker 重启、GUI Movie 重启、bootstrap 重跑都不能解决当前会话问题。
4. `dde execute` 和旧 `send` 链的结构差异是什么。
5. 目前已验证成功的替代控制面到底能做什么。

## 8.1 send IPG-MOVIE 长期失稳的已知故障型态

截至 2026-05-09，已经能稳定区分下面这类故障：

1. `TclEval` / `RunScript` 仍然正常
2. `WInfoInterps "IPG-MOVIE"` 仍然能返回 `IPG-MOVIE`
3. 但最小 `send IPG-MOVIE { list ok [info patchlevel] }` 也会失败，错误为 `remote server cannot handle this command`

这说明故障点不在 Python 到 CarMaker 的 DDE，也不在“系统里完全找不到 Movie 解释器名”，而是在 `IPG-MOVIE` 的 Tk send 执行面本身已经挂住。

当前经验结论：

1. 只重开可见的 CarMaker / IPG-MOVIE 窗口不一定能清掉这个状态
2. 需要做“全栈硬重建”，至少把 `CarMaker.win64.exe`、GUI `Movie.exe` 和 headless `Movie.exe -mode GPUSensor` 一起清掉再拉起
3. 仅在重启电脑后恢复，通常意味着会话里还有更底层的残留进程/注册状态没有被可见窗口重启覆盖

当前推荐恢复动作，不要再只做手工重开窗口：

```powershell
c:/CM_Projects/CMO141_Calibration/.venv/Scripts/python.exe Data/Script/CameraCalibration/cmapi_testrun_control.py \
    --testrun vctc_ngxpro \
    --open-movie \
    --clean-existing-processes \
    --health-check-after-start \
    --keep-carmaker-open \
    --keep-movie-open
```

这条链的作用是：

1. 先清掉现有 CarMaker / Movie 进程栈，而不是只重开前台窗口
2. 重新拉起 CarMaker 和 IPG-MOVIE
3. 启动后立刻跑 DDE 健康检查
4. 如果 `send IPG-MOVIE` 仍然坏，直接在恢复阶段失败，而不是等到标定中途才暴露

## 8.1.1 当前 open_movie 启动链记录

截至 2026-05-10，`cmapi_testrun_control.py --open-movie` 的当前启动链按下面顺序执行：

1. 复用或拉起 CarMaker GUI，优先走 `GUI/HIL.exe`
2. 等 `TclEval/CarMaker` 可用
3. 用 `LoadTestRun` 同步 GUI 里真正选中的 TestRun
4. 通过 Tcl 执行一次手工等价 bootstrap：`StartSim -> WaitForStatus running -> StopSim -> WaitForStatus idle`
5. 非 `--open-movie` 的普通启动链也统一改为同一套 Tcl `StartSim` / `StopSim` 控制，不再以 `SimControlInteractive.start_sim()` / `stop_sim()` 作为主路径
6. 复用或拉起 GUI `Movie.exe -cmgui CarMaker`
7. 优先用 `send IPG-MOVIE` 主动探测当前 camera/view 是否就绪
8. 如果连续两次 `send IPG-MOVIE` 失败，先尝试只重启 GUI Movie 一次，再重新探测
9. 如果 GUI Movie 重启后 `send IPG-MOVIE` 仍然失败，才退回到较弱的 runtime fallback：`WInfoInterps "IPG-MOVIE"` + `WInfoInterps "GPUSensor_*"` + GUI/GPUSensor Movie 进程同时存在
10. 如果传入 `--health-check-after-start`，启动链结束前再跑一轮只读 DDE 健康检查，并把 `send IPG-MOVIE` 失败直接视为启动失败

这里的关键变化是：

1. 启动链不再依赖固定 `45s` 睡眠
2. `send IPG-MOVIE` 不再只是记录失败，而是会先尝试一次 GUI Movie 自恢复
3. 启动后健康检查使用只读探针，不再允许 `movie_command_probe` 里偷偷触发 `Movie start`

### 8.1.2 当前已验证的恢复边界

截至 2026-05-10 当前会话，下面这些代码级恢复动作都已经实测过：

1. 仅重启 GUI Movie
2. 清掉全部 `Movie.exe` 后，重新跑一次 bootstrap，再拉起 GUI Movie
3. 即使 `RunScript Exec` 返回假阴性，也继续等结果文件，不再把 transport 噪音误判成 bootstrap 失败

在上述修正全部生效后，当前会话里最小 `send IPG-MOVIE { list ok [info patchlevel] }` 仍然稳定报：

```text
remote server cannot handle this command
```

因此当前代码结论应明确为：

1. 启动链已经能更早、更干净地识别坏态
2. 启动链已经尝试了代码层可控的 Movie 自恢复
3. 如果 `--health-check-after-start` 仍然失败，当前更可能是 Windows 登录会话级的 Tk send/DDE 状态坏掉，而不是脚本里少做了一步 Movie 重启

### 8.1.3 当前证据链整理

截至 2026-05-10，围绕 `send IPG-MOVIE` 坏态，当前可以稳定复述的证据链如下。

1. 健康基线是明确存在的，不是“这个工程一直都不稳定”
2. 健康时的判据已经固定：`WInfoInterps "IPG-MOVIE"` 能返回 `IPG-MOVIE`，最小 `send IPG-MOVIE { list ok [info patchlevel] camera $Camera::v(Name) }` 能返回 `ok ... camera ...`
3. 当前坏态下，`TclEval/CarMaker` 往往仍然正常，`WInfoInterps` 也往往仍能解析到 `IPG-MOVIE`，有时还能同时解析到 `GPUSensor_1_0`
4. 但最小 `send IPG-MOVIE` 会失败，且失败形态会在同一登录会话里漂移，例如：
    - `remote server cannot handle this command`
    - `dde command failed`
    - `invalid data returned from server`
5. 当前最新一次更宽的坏态已落在 `movie_send_targets_unresponsive`：CarMaker 仍能解析到 `IPG-MOVIE` 和 `GPUSensor_1_0`，但对这两个目标的 `send` 都失败
6. 因此故障点已可收缩到 Movie 侧解释器的 `Tk send` 执行面，而不是 Python 到 CarMaker 的 DDE 主链

当前已掌握的直接证据包括：

1. 历史健康基线：`project_notes/ipgmovie-health-normal-2026-05-09.md`
2. 当前会话内多轮坏态与修复尝试：`project_notes/ipgmovie-pre-reboot-snapshot-2026-05-10.md`
3. 最新只读健康快照：`SimOutput/ipgmovie_health_monitor/20260510_140501/first_failure.json`

对当前恢复边界的判断应继续保持保守：

1. 代码层恢复已经覆盖 GUI Movie 重启、全 Movie 栈重建、bootstrap 重跑、启动后只读健康复检
2. 当前会话级实验已经覆盖 IME/TextInputHost 扰动、GUI Movie 单独重启、Explorer/壳层扰动
3. 这些动作可以让坏态“变形”，甚至短暂收窄，但尚未形成稳定、可重复的当前会话恢复
4. 因此“注销/重启能恢复”目前仍应理解为：它重建了整个 Windows 交互会话，而不是单纯比脚本多做了一次 Movie 重启

## 8.2 非 RPA 离屏控制与取图边界

当前工程里，非 RPA 路线已经存在，但要区分“后台控制”与“离屏取图”。

### 8.2.1 非 RPA 后台控制主链

当前已验证可用的非 RPA 后台控制主链是：

1. Python 通过 DDE 连接 `TclEval/CarMaker`
2. 执行 `RunScript { ... }`
3. 在 Tcl 内用 `send IPG-MOVIE { ... }` 把命令送进 IPG-MOVIE 解释器

这条路径的性质已经明确：

1. 不依赖鼠标点击
2. 不依赖键盘输入
3. 不要求 IPG-MOVIE 在前台
4. 窗口最小化时仍可工作

因此，对“有没有不走 RPA 的控制方式”的回答是：有，而且这一直是当前工程的主控链。

### 8.2.2 非 RPA 离屏取图主链

当前已验证可工作的非 RPA 离屏取图路径不是窗口截图，也不是 RPA，而是：

1. 在 `send IPG-MOVIE` 内创建外层 `captureFBO`
2. 在该 FBO 作用域里执行 `UpdateView $vno`
3. 再用 `gl bindframebuffer_read` 加 `gl readpixels` 导出图像

当前工程里的经验结论是：

1. 早期直接读默认 framebuffer 会得到黑图
2. 早期直接读 `View(FBO.tex)` 或手工重放错误显示列表会拿到错误图形上下文
3. 现阶段真正可用的是 `captureFBO -> UpdateView -> readpixels`
4. 这条路径已实测能得到正确的 rear_tv 离屏图，而且执行期间不会把前台切到 IPG-MOVIE

### 8.2.3 当前没有已验证成功的“绕开 send”的第二控制面

虽然存在非 RPA 的后台控制和离屏取图，但它们当前都仍依赖 `send IPG-MOVIE` 是健康的。

截至 2026-05-10，当前没有已验证成功的替代主链能够在 `send IPG-MOVIE` 坏掉时继续稳定工作：

1. `SaveImage_export` 只是编码器，不会自己抓当前视图
2. 试图把同类逻辑改发到 `GPUSensor_1_0`，当前并没有形成稳定可用的替代控制面
3. 因此一旦 `send IPG-MOVIE` 进入坏态，非 RPA 离屏控制与非 RPA 离屏取图通常会一起失效

当前最准确的说法应是：

1. 有非 RPA 路线
2. 有可工作的离屏取图路线
3. 但目前没有一个“已验证可用、且能完全绕开 `send IPG-MOVIE` 坏态”的第二控制面

## 8.3 2026-05-09 当前健康基线快照

后续如果用户说“现在又不正常了”，优先按下面这一组基线做对比。

### 8.3.1 send 健康口径

同一口径下，当前健康态满足：

1. `WInfoInterps "IPG-MOVIE"` 成功返回 `IPG-MOVIE`
2. 最小 `send IPG-MOVIE` 成功
3. 返回 payload 里能读到 Tcl 版本和当前 camera

2026-05-09 的实测结果是：

```text
winterps_rc 0
winterps_msg IPG-MOVIE
send_rc 0
send_msg {ok 8.6.9 camera {CAMERA_RSI-SENSOR Vhcl.Side_Right_Rear}}
```

对应探针结果文件：

`SimOutput/dde_recovery_probe/probe_send_ipgmovie_compare.txt`

### 8.2.2 当前进程层快照

2026-05-09 当前健康态下，相关进程是：

1. `CarMaker.win64.exe`
2. `Movie.exe`，窗口标题 `GPUSensor - 'kel' online`
3. `Movie.exe`，窗口标题 `IPGMovie - 'kel' online`

对应启动时间快照：

1. `CarMaker.win64.exe`：17:44:49
2. `Movie.exe (GPUSensor)`：17:44:50
3. `Movie.exe (IPGMovie)`：17:59:20

二进制路径：

1. `D:/IPG/carmaker/win64-14.1/bin/CarMaker.win64.exe`
2. `D:/IPG/carmaker/win64-14.1/GUI/Movie.exe`

文件版本：

1. `Movie.exe`：`14,1,0,0`
2. 当前 `CarMaker.win64.exe` 文件版本资源为空

### 8.2.3 当前 Tcl/Tk 运行时快照

通过 `TclEval -> send IPG-MOVIE` 读取到的当前健康基线如下：

CarMaker 侧：

```text
cm_patchlevel 8.6.9
cm_tk_patchLevel 8.6.9
cm_windowingsystem win32
cm_executable D:/IPG/carmaker/win64-14.1/GUI/HIL.exe
cm_platform_os Windows NT
cm_platform_osVersion 10.0
cm_platform_platform windows
cm_interps IPG-MOVIE
```

IPG-MOVIE 侧：

```text
ipg_patchlevel 8.6.9
ipg_tk_patchLevel 8.6.9
ipg_windowingsystem win32
ipg_executable D:/IPG/carmaker/win64-14.1/GUI/Movie.exe
ipg_platform_os Windows NT
ipg_platform_osVersion 10.0
ipg_platform_platform windows
ipg_send_command_exists 1
ipg_current_camera CAMERA_RSI-SENSOR Vhcl.Side_Right_Rear
```

对应探针结果文件：

`SimOutput/dde_recovery_probe/probe_ipgmovie_runtime_baseline_retry.txt`

### 8.2.4 当前驱动层快照

当前系统枚举到的视频控制器/驱动版本：

1. `NVIDIA GeForce RTX 4060 Laptop GPU`：`32.0.15.8195`
2. `Intel(R) UHD Graphics`：`32.0.101.7084`
3. `OrayIddDriver Device`：`17.50.19.949`

这三项都建议纳入后续异常对比，尤其要注意远控/虚拟显示驱动 `OrayIddDriver Device` 是否参与了当前桌面会话。

### 8.2.5 OpenGL 命令面快照

当前运行时里 `gl` 命令存在，但直接尝试 `gl version` 或 `gl getstring ...` 都失败。

这说明当前 IPG-MOVIE 暴露的是一组 Tcl 封装过的 `gl` 子命令，但并没有直接暴露标准 `glGetString` 风格接口。因此后续如果要对比 OpenGL 层，不能假设能直接从 Tcl 里拿到 `vendor` / `renderer` / `version` 字符串。

对应探针结果文件：

`SimOutput/dde_recovery_probe/probe_ipgmovie_gl_runtime.txt`

### 8.2.6 一条额外诊断现象

在记录这批基线时，曾出现过一次瞬时 `ConnectTo("TclEval", "CarMaker")` 失败；但紧接着复跑最小 `send IPG-MOVIE` 探针就恢复正常。

当前把它记为“偶发 DDE 连接抖动”，不要直接等同于 `send IPG-MOVIE` 主链故障；后续若再遇到一次性 `ConnectTo failed`，应先立刻复跑最小健康探针，再判断是否真的脱离基线。

### 8.2.7 后续对比建议

如果后续再次失稳，先重复同一口径：

1. `WInfoInterps "IPG-MOVIE"`
2. 最小 `send IPG-MOVIE { list ok [info patchlevel] camera $Camera::v(Name) }`
3. `CarMaker.win64.exe`、`Movie.exe (GPUSensor)`、`Movie.exe (IPGMovie)` 是否仍在，是否被重建，是否缺失某一个
4. Tcl/Tk patchlevel、windowingsystem、executable 路径是否变化
5. 显卡驱动与虚拟显示驱动是否变化

如果再次出现：

1. `WInfoInterps "IPG-MOVIE"` 仍然返回 `IPG-MOVIE`
2. 但最小 `send IPG-MOVIE` 失败并报 `remote server cannot handle this command`

则仍可判断故障点在 `IPG-MOVIE` 的 Tk send 执行面，而不是 Python 到 CarMaker 的 DDE 入口。

## 9. View -> Size -> Custom 控制结论

### 9.1 菜单项绑定

`IPG-MOVIE -> View -> Size -> Custom...` 当前主窗口菜单项位于：

`.view0.mbar.view.m.size`

已验证的菜单项列表里，`Custom...` 为：

1. `index=9`
2. `command={EventCallbacks::View::On_Set_Size "" "" activeview}`

同一菜单里的固定尺寸项，例如 `400x300`，绑定形式为：

```tcl
EventCallbacks::View::On_Set_Size 400 300 activeview
```

### 9.2 调用链

已验证的过程转发链如下：

1. `EventCallbacks::View::On_Set_Size ...`
2. `SetViewSizeCurrentView`
3. `View::SetSize width height .viewN`

其中 `SetViewSizeCurrentView` 的主体是：

```tcl
global View
scan $View(ev.view) %d wno
View::SetSize "" "" .view$wno
```

### 9.3 Custom 的 width / height 从哪里来

`View::SetSize` 已验证会在 `width==""` 时弹出一个复用当前 view 的尺寸设置对话框：

```tcl
set w $wv.vspopup
TopLevel $w [L ViewSize]
grid [entry $w.width ...] [entry $w.height ...]
$w.width  insert 0 [$wv.gl0 cget -width]
$w.height insert 0 [$wv.gl0 cget -height]
if {[DialogWaitOkCancel $w $w.btn] == "ok"} {
    catch {View::SetSize [$w.width get] [$w.height get] $wv}
}
```

所以 `Custom...` 的 width / height 不是从 `View($vno)` 字典里直接取，而是：

1. 先从当前视图 widget `$wv.gl0` 的 `-width` / `-height` 预填到输入框
2. 用户确认后再回调 `View::SetSize <width> <height> $wv`

### 9.4 真正被改动的量

已做可逆验证：

```tcl
View::SetSize 400 300 .view0
```

读回结果为：

1. `orig=960x640`
2. `test=400x300`
3. `pref=400x300`
4. `saved=400 300 960x690+89+39`
5. `restored=960x640`

这说明真正被改的是：

1. `.view0.gl0` 的 widget 宽高
2. `Pref(Window.view0.width)`
3. `Pref(Window.view0.height)`
4. `View(SavedSize.0)`

而不是直接把 `dict get $View($vno) Width/Height` 当作唯一控制面。

### 9.5 当前推荐的后台控制方式

如果目标是后台直接控制 `View -> Size -> Custom...` 的 width / height，当前更直接、可控的方式不是去点菜单 `Custom...`，而是直接调用：

```tcl
View::SetSize <width> <height> .view0
update
update idletasks
```

例如：

```tcl
View::SetSize 400 300 .view0
```

如果想按“当前激活 view”来写，可以先解析活动窗口号：

```tcl
scan $View(ev.view) %d wno
View::SetSize 400 300 .view$wno
```

### 9.6 当前理解

截至 2026-05-08，关于 `View -> Size -> Custom...` 可确认：

1. 菜单入口存在，绑定到 `EventCallbacks::View::On_Set_Size "" "" activeview`
2. Custom 模式本质上是打开 `.$view.vspopup` 对话框收集 width / height
3. 真正执行尺寸更新的是 `View::SetSize`
4. `View::SetSize` 会更新 view widget 尺寸以及 `Pref(Window.viewN.width/height)`、`View(SavedSize.N)`
5. 后台 DDE 下如果要稳定控制 width / height，优先直接调用 `View::SetSize`，不要依赖弹出 `Custom...` 对话框

## 10. 打开 IPG-MOVIE -> Camera -> Settings 的方式

### 10.1 菜单项绑定

当前主窗口的 Camera 菜单位于：

`.view0.mbar.camera.m`

已验证 `Camera -> Settings...` 菜单项为：

1. `index=3`
2. `label=Settings...`
3. `command=Camera::ShowSettingsDlg`

也就是说，菜单入口本身就直接绑定到：

```tcl
Camera::ShowSettingsDlg
```

### 10.2 当前推荐的后台打开方式

如果目标是在后台打开 IPG-MOVIE 的 Camera Settings，对应 Tcl 入口就是：

```tcl
send IPG-MOVIE {
    Camera::ShowSettingsDlg
    update
    update idletasks
}
```

如果担心该命令进入自身 UI 事件流导致当前脚本阻塞，可以异步调度：

```tcl
send IPG-MOVIE {
    after 0 Camera::ShowSettingsDlg
}
```

当前验证里，这种异步调度方式能稳定返回，并把 `.camera` 窗口拉起。

### 10.3 已验证结果

已通过运行时探针确认：

1. `after 0 Camera::ShowSettingsDlg` 可以成功调度打开动作
2. 打开后的窗口路径是 `.camera`
3. 窗口标题是 `IPGMovie - Camera Settings`
4. 打开后窗口状态为 `normal`

也就是说，当前工程里要打开 `IPG-MOVIE -> Camera -> Settings`，优先直接使用：

`Camera::ShowSettingsDlg`

而不是去模拟鼠标点菜单。

## 11. 后台切到 Camera Settings 里的 lens 页面

### 11.1 当前运行时结构

当前运行时探针已确认：

1. Camera Settings 窗口路径是 `.camera`
2. lens 参数窗口路径是 `.camera.cammoddlg`
3. `.camera.cammoddlg` 是一个单独的 `Toplevel`
4. 其标题为 `IPGMovie - Camera Lens Parameters`
5. 在未显示时，状态通常为 `withdrawn`

当前 `.camera` 第一层 child 中，与 lens 最相关的入口在：

`.camera.fmore`

该容器下存在：

1. `.camera.fmore.lens`
2. `.camera.fmore.llens`
3. `.camera.fmore.bcammod`

### 11.2 已验证的后台打开 lens 入口

已验证 `.camera.fmore.bcammod invoke` 会把 `.camera.cammoddlg` 从 `withdrawn` 拉到 `normal`。

本质上，这说明 Camera Settings 里的 lens 页面/镜头参数窗口，标准后台入口就是：

```tcl
.camera.fmore.bcammod invoke
update
update idletasks
```

### 11.3 已初始化后的更直接入口

如果 `.camera.cammoddlg` 已经存在，那么比走按钮更直接的后台方式是：

```tcl
wm deiconify .camera.cammoddlg
update
update idletasks
```

已验证结果：

1. `before=withdrawn`
2. `after=normal`
3. `title=IPGMovie - Camera Lens Parameters`

所以对于“已经初始化过一次 lens 窗口”的会话，后台切到 lens 页面，优先直接 `wm deiconify .camera.cammoddlg`。

### 11.4 当前推荐顺序

如果只想保证 lens 相关 widget 树可用，当前推荐顺序是：

1. 先确保 `.camera` 存在：`Camera::ShowSettingsDlg`
2. 若 `.camera.cammoddlg` 尚未存在或尚未初始化，走 `.camera.fmore.bcammod invoke`
3. 若 `.camera.cammoddlg` 已存在，则直接 `wm deiconify .camera.cammoddlg`

可参考 Tcl 片段：

```tcl
send IPG-MOVIE {
    Camera::ShowSettingsDlg
    update
    update idletasks
    if {[winfo exists .camera.cammoddlg]} {
        wm deiconify .camera.cammoddlg
    } elseif {[winfo exists .camera.fmore.bcammod]} {
        .camera.fmore.bcammod invoke
    }
    update
    update idletasks
}
```

## 12. 最小化状态下打开 Camera Settings 并保持无前台打断

### 12.1 当前可确认的约束

当前验证里有一个重要限制：

`.camera` 是 transient 窗口，不能 `iconify`

已验证直接执行：

```tcl
wm iconify .camera
```

会报：

`can't iconify ".camera": it is a transient`

所以“最小化 Camera Settings”在当前语义下，正确动作不是 `iconify`，而是 `withdraw`。

### 12.2 无前台打断的可行流程

对于“后台打开 Camera Settings，并尽量不打断前台”的场景，当前更稳的做法是两段式：

第一段：后台打开并完成需要的初始化

```tcl
after 0 {
    Camera::ShowSettingsDlg
    update
    update idletasks
    if {[winfo exists .camera.fmore.bcammod]} {
        .camera.fmore.bcammod invoke
        update
        update idletasks
    }
}
```

第二段：在后续单独一条命令中，把这些 transient/toplevel 收回去

```tcl
if {[winfo exists .camera.cammoddlg]} {wm withdraw .camera.cammoddlg}
if {[winfo exists .camera]} {wm withdraw .camera}
update
update idletasks
```

### 12.3 已验证结果

两段式流程已验证可以把窗口最终收回：

1. 收回前：`before_cam=normal`, `before_lens=normal`
2. 收回后：`after_cam=withdrawn`, `after_lens=withdrawn`

这说明：

1. Camera Settings 可以后台打开
2. lens 参数窗口也可以后台打开
3. 之后可以通过 `withdraw` 收回，避免长期停留在桌面上

### 12.4 当前建议

如果目标是“初始化 widget 树，但不要求用户肉眼看到窗口一直停在桌面上”，当前建议是：

1. 用 DDE 后台触发 `Camera::ShowSettingsDlg`
2. 用 `.camera.fmore.bcammod invoke` 或 `wm deiconify .camera.cammoddlg` 进入 lens 参数窗口
3. 初始化完成后，对 `.camera` 和 `.camera.cammoddlg` 都执行 `wm withdraw`

这一条比尝试 `iconify .camera` 更可靠，因为 `.camera` 本身是 transient。

## 13. 选择 IPG-MOVIE -> Camera -> Sensors -> 某个 sensor 的方式

### 13.1 菜单路径与真实回调

当前运行时探针已确认：

1. Camera 菜单路径是 `.view0.mbar.camera.m`
2. Sensors 子菜单路径是 `.view0.mbar.camera.m.sens`

当前会话中，`Sensors` 子菜单里的条目为：

1. `label=CAMERA_RSI-SENSOR Vhcl.Side_Right_Rear`
2. `command={Camera::Select {CAMERA_RSI-SENSOR Vhcl.Side_Right_Rear}}`

也就是说，`Camera -> Sensors -> 某个 sensor` 的真实入口，不是单独另一套 API，而是仍然走：

```tcl
Camera::Select {<sensor name>}
```

### 13.2 当前会话已验证的 sensor 名

当前会话里读到的活动 camera/sensor 名为：

`CAMERA_RSI-SENSOR Vhcl.Side_Right_Rear`

因此在当前环境下，直接选择该 sensor 的 Tcl 写法是：

```tcl
send IPG-MOVIE {
    Camera::Select {CAMERA_RSI-SENSOR Vhcl.Side_Right_Rear}
    update
    update idletasks
}
```

### 13.3 Camera::Select 的调用签名

运行时已验证：

1. `Camera::Select` 的参数是 `Name` 和 `vno`
2. 两个参数都有默认值 `{}`

即：

```tcl
Camera::Select Name ?vno?
```

其中：

1. `Name` 是 Camera 菜单项或 sensor 名
2. `vno` 留空时，作用于当前活动 view

### 13.4 当前理解

截至当前验证，`Camera` 主菜单下的普通视角项与 `Sensors` 子菜单项，控制面是统一的：

1. 普通项例如 `Bird's Eye View` 走 `Camera::Select {Bird's Eye View}`
2. Sensors 里的具体传感器也走 `Camera::Select {CAMERA_RSI-SENSOR ...}`
3. `Sensors` 子菜单当前按实测是 command 项，不提供独立的勾选态
4. 仅执行 `Camera::Select` 只能把当前 camera 名称和 Camera Settings 当前项切到目标 sensor，不足以保证主视图已经切成该 sensor 视角
5. 若要把目标 sensor 真正应用到当前 view，还要补一条 Camera Settings 的 `Add/Set` 动作：`.camera.btn.set invoke`

所以如果后续想后台切到某个 sensor，当前推荐顺序是：

```tcl
Camera::ShowSettingsDlg
update
update idletasks
Camera::Select {<exact sensor label>}
update
update idletasks
if {[winfo exists .camera.btn.set]} {
    .camera.btn.set invoke
}
update
update idletasks
```

其中：

1. `Camera::Select` 负责把目标 sensor 选成当前 camera 项
2. `.camera.btn.set invoke` 负责把该 camera 真正应用到活动 view
3. 不需要模拟点开 `Camera -> Sensors` 菜单，但也不能把 `Camera::Select` 误当成完整的可见视角切换动作