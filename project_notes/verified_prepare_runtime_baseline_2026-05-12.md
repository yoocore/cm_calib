# 已验证 Prepare 运行链冻结基线（2026-05-12）

本文档用于固化本轮已经实测通过的 Prepare 关键运行链，作为后续排障、回归和实现对齐的 knowhow 与基准。

配套参考脚本：

1. `../verify_runtime_chain_baseline.py` 用于保存本轮已验证的独立运行链实现，作为回归对照、参考模板和后续实现对齐样板。

冻结原则：

1. 本文档只记录已经跑通过的基线链路和验收口径。
2. 除非用户明确要求，不再直接改写本文档。
3. 后续如果出现新的试验链路，应另起补充文档，不覆盖本基线。

## 1. 适用范围

本文档冻结的是 9.4 CM Prepare 中已经逐步实测通过的核心运行链，重点覆盖以下步骤：

1. TestRun 启停
2. IPG-MOVIE 打开与 scene ready
3. ABRAXAS 打开
4. sensor 切换
5. view size 设置
6. Camera Settings 打开
7. Lens Parameters 打开
8. DDE 健康检查

不在本文档冻结范围内的内容：

1. 物理点击路径
2. `SimControlInteractive.start_sim()` / `stop_sim()`
3. 未经本轮实测的新控制方式

## 2. 前置条件

执行本基线前，应满足以下前提：

1. CarMaker 工程目录和目标 TestRun 已确定。
2. 目标 camera 的 `camera.<sensor>.json` 已存在。
3. 配置中的 `real_image` 可读。
4. `TclEval/CarMaker` DDE 服务可连接。
5. GUI `Movie.exe` 和 `GPUSensor Movie.exe` 由运行链复用或拉起。

## 3. TestRun 启停冻结口径

TestRun 启停保留两种冻结基线，后续不要再扩展第三套主语义。

### 3.1 方式 A：纯 Tcl StartSim/StopSim

这是当前实现主路径，也是本轮 Prepare 主验证链使用的方式。

Tcl 片段如下：

```tcl
StartSim
update
update idletasks
WaitForStatus running 10000
StopSim
update
update idletasks
WaitForStatus idle 10000
```

运行语义：

1. `StartSim` 后必须等待 `running`。
2. `StopSim` 后必须等待 `idle`。
3. 如果任一 `WaitForStatus` 失败，则该次 bootstrap 失败。

### 3.2 方式 B：Tcl/Tk 控件层 invoke

这是保留的备用冻结基线，语义上属于 Tcl/Tk 控件调用，不属于物理点击。

Tcl 片段如下：

```tcl
if {![winfo exists .f.btn.start]} {
    error "missing widget .f.btn.start"
}
.f.btn.start invoke
update
update idletasks
WaitForStatus running 10000

if {![winfo exists .f.btn.stop]} {
    error "missing widget .f.btn.stop"
}
.f.btn.stop invoke
update
update idletasks
WaitForStatus idle 10000
```

运行语义：

1. 仍然必须配对 `WaitForStatus running` 和 `WaitForStatus idle`。
2. 该方式是控件层 `invoke`，不是鼠标点击，不归类为物理点击路径。
3. 后续若主实现不使用该方式，也要把它作为冻结对照链保留。

## 4. 已验证 Prepare 顺序

当前已经逐步实测通过的顺序如下。

### 4.1 TestRun bootstrap

1. 先让 CarMaker GUI 处于目标 TestRun。
2. 执行一次 TestRun bootstrap。
3. 当前主验证链使用方式 A：纯 Tcl `StartSim -> WaitForStatus running -> StopSim -> WaitForStatus idle`。

### 4.2 打开 IPG-MOVIE 并等待 scene ready

当前验收口径：

1. IPG-MOVIE 在线。
2. 运行态可回读当前 `camera_name`。
3. 运行态可回读 view 尺寸。
4. ABRAXAS 菜单链可访问。

## 5. 已验证的 IPG-MOVIE 控制链

### 5.1 打开 ABRAXAS

当前冻结口径：

1. `View -> Show -> ABRAXAS` 对应 `View(ABRAXAS)`。
2. 控制层优先走真实菜单回调，而不是只写状态位。

可用 Tcl 片段：

```tcl
set menu .view0.mbar.view.m.show
if {[$menu index end] < 1} {
    error "ABRAXAS menu item missing"
}
if {[$menu entrycget 1 -variable] ne "View(ABRAXAS)"} {
    error "ABRAXAS menu binding changed"
}
if {[set View(ABRAXAS)] != 1} {
    $menu invoke 1
}
update
update idletasks
```

### 5.2 sensor 切换

当前冻结口径：

1. `Camera -> Sensors` 是 command 菜单，不以菜单勾选态作为验收依据。
2. 成功标准是目标 sensor 成为当前 camera。

可用 Tcl 片段：

```tcl
Camera::Select {CAMERA_RSI-SENSOR Vhcl.rear_tv}
update
update idletasks
```

验收时只看当前 camera 名称，不看菜单对勾。

### 5.3 view size 设置

当前冻结口径：

1. view size 运行时直接从目标配置里的 `real_image` 读取。
2. `camera.<sensor>.json` 不再保留 `movie_view_width` 和 `movie_view_height`。
3. 对 rear_tv 的当前实测结果是 `1920 x 1536`。

### 5.4 Camera Settings 打开

当前冻结口径：

1. 后台入口是 `Camera::ShowSettingsDlg`。
2. 验收标准是 `.camera` 存在，并能达到 `title=IPGMovie - Camera Settings`。

可用 Tcl 片段：

```tcl
Camera::ShowSettingsDlg
update
update idletasks
if {[winfo exists .camera]} {
    wm deiconify .camera
}
update
update idletasks
```

### 5.5 Lens Parameters 打开

当前冻结口径：

1. 首次初始化可走 `.camera.fmore.bcammod invoke`。
2. 已存在窗口时优先 `wm deiconify .camera.cammoddlg`。
3. 验收标准是 `.camera.cammoddlg` 存在，并能达到 `title=IPGMovie - Camera Lens Parameters`。

可用 Tcl 片段：

```tcl
if {[winfo exists .camera.cammoddlg]} {
    wm deiconify .camera.cammoddlg
} elseif {[winfo exists .camera.fmore.bcammod]} {
    .camera.fmore.bcammod invoke
}
update
update idletasks
```

## 6. DDE 健康检查冻结口径

当前冻结口径：

1. Prepare 链最后必须执行只读 DDE 健康检查。
2. 当前验收标准是 `all_ok = true`。
3. 检查项至少包括：`tcleval_ping`、`interpreter_probe`、`movie_command_probe`、`movie_ping`、`movie_camera_probe`、`movie_view_probe`、`gpusensor_ping`。

本轮实测通过结果：

1. `all_ok = true`
2. classification code = `ok`
3. `movie_camera_probe = CAMERA_RSI-SENSOR Vhcl.rear_tv`
4. `movie_view_probe = 1920 x 1536`

## 7. 当前冻结验收结果

截至 2026-05-12，本轮已经逐步实测通过的结果如下：

1. TestRun 纯 Tcl bootstrap 通过。
2. IPG-MOVIE scene ready 通过。
3. ABRAXAS 打开通过。
4. sensor 切换通过，按“当前 camera”判定成功。
5. view size 设置通过，尺寸从 `real_image` 读取。
6. Camera Settings 打开通过，状态可达 `normal`。
7. Lens Parameters 打开通过，状态可达 `normal`。
8. 只读 DDE 健康检查通过。

## 8. 使用约束

后续讨论或改代码时，如果要回答“当前已验证的 Prepare 完整基线是什么”，默认以本文档为准。

如果后续实现与本文档不一致，应先说明是：

1. 兼容本文档的实现细化。
2. 对本文档之外的新试验链。
3. 需要明确用户批准后才能替换本基线的行为变更。