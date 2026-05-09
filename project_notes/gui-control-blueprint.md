- 本项目本地单用户控制台优先方案：主控层用 exe gui，不做 web-first；web 若需要，只作为第二阶段的历史结果/图表展示层。
- GUI 技术建议：PySide6 + QProcess。现有 Python CLI 脚本保持为唯一真实控制层，GUI 第一阶段只做本地编排与观测，不内嵌重写标定算法。
- 底层优先复用 `Data/Script/CameraCalibration/cmapi_testrun_control.py`、`Data/Script/CameraCalibration/camera_calibration.py`、`Data/Script/CameraCalibration/dde_health_check.py`。
- 第一阶段 GUI 目标：1) 管理 CarMaker 与 GUI IPG-MOVIE 单实例复用；2) 选择 TestRun、解析 Vehicle、切单 camera sensor；3) 启停并监控一次 calibration；4) 展示状态、日志、输出目录、最后一次 best result。
- 建议新增目录：`Data/Script/CameraCalibration/gui_app/`，分为 `app.py`、`main_window.py`、`controllers/`、`services/`、`models/`、`widgets/`。
- 推荐界面分三栏：Runtime（project/TestRun/Vehicle/sensor/CarMaker/Movie 状态与 prepare 控制）、Calibration（config/testrun/sensor/rounds 与 start/stop/smoke）、Output（实时日志、output_dir、best_score、best_values 摘要）。
- 第一阶段关键交互流：runtime status 探测；prepare runtime；start calibration。先做运行态探测与准备，再接 calibration 启停。
- GUI 内部建议显式状态机：`idle`、`runtime_unknown`、`runtime_ready`、`calibration_running`、`calibration_finished`、`calibration_failed`。
- 为 GUI 干净接入，建议后续给 `cmapi_testrun_control.py` 增加 `status` / `prepare` 这类 machine-readable CLI 模式，并让 `camera_calibration.py` 在结束时额外输出一行 JSON summary。
- 里程碑建议：M1 运行态面板；M2 prepare runtime；M3 标定任务托管；M4 配置选择/最近使用/输出目录/错误提示收口。

-----------------------------------------------------------------------------------------------------------------------------------
主方案定成：

1. 主控层用 exe gui
2. 底层控制继续复用现有 Python 脚本
3. 第一阶段不改现有标定算法主线，只做一层稳定的本地编排与观测
4. 第二阶段如果确实需要更强的历史浏览，再补本地 web dashboard，而不是一开始就上 web 主控

**目标**
做一个本机单用户的标定控制台，解决四件事：

1. 管理 CarMaker 和 GUI IPG-MOVIE 单实例复用
2. 选择 TestRun、解析 Vehicle、切单 camera sensor
3. 启动、停止、监控一次标定任务
4. 可视化当前状态、日志、结果目录、最后一次 best result

底层能力尽量复用已有实现：
cmapi_testrun_control.py
camera_calibration.py
dde_health_check.py

**技术选型**
建议这样定：

1. GUI 框架：PySide6
2. 任务执行：QProcess，不要把长任务直接塞进 GUI 线程
3. 底层调用方式：优先子进程调用现有脚本，不先做深度 import 内嵌
4. 配置来源：直接读现有 camera.xxx.json
5. 日志展示：实时抓 stdout/stderr 并写本地 run log
6. 打包：后续用 PyInstaller；第一阶段先不打包，先跑 python 启动

这里特意选 QProcess 而不是“直接 import 后在 GUI 里调用”，因为你现有脚本已经是完整的 CLI 工作单元。先把它们当稳定黑盒用，隔离性更好，也更容易排错。

**整体分层**
建议拆成 4 层。

1. Presentation
桌面窗口、按钮、表单、日志面板、状态灯。

2. Application
把用户动作翻译成一个个任务：
运行健康检查
准备运行环境
切 camera sensor
启动或复用 CarMaker
启动或复用 GUI Movie
启动 calibration
停止 calibration

3. Domain
统一定义运行状态、任务状态、进程状态、配置模型。

4. Infrastructure
真正落地到：
Python 子进程
PowerShell 进程探测
读取 JSON
读取 result.json
读取输出目录
调用现有脚本

**建议目录**
第一阶段建议只新增一个独立目录，不去大改现有标定主线：

Data/Script/CameraCalibration/gui_app/

里面建议放这些模块：

1. app.py
程序入口，创建 QApplication 和主窗口

2. main_window.py
主窗口和页面布局

3. controllers/runtime_controller.py
运行态控制：CarMaker、Movie、TestRun、sensor

4. controllers/calibration_controller.py
标定任务控制：启动、停止、读取日志、读取结果

5. services/process_service.py
统一封装 QProcess 和子进程生命周期

6. services/runtime_service.py
调用 cmapi_testrun_control.py、dde_health_check.py、PowerShell 探针

7. services/calibration_service.py
调用 camera_calibration.py，管理运行日志和结果目录

8. services/config_service.py
读取 camera.xxx.json，提取 camera 名、output_dir、参数摘要

9. services/result_service.py
扫描 SimOutput，读取最新 result.json、campaign_summary.json、run log

10. models/state.py
应用状态对象和枚举

11. widgets/runtime_panel.py
运行控制面板

12. widgets/calibration_panel.py
标定参数和启动面板

13. widgets/log_panel.py
实时日志面板

14. widgets/result_panel.py
结果摘要面板

**第一版 UI 布局**
不要做复杂多页，先做单窗口三栏就够。

1. 左栏：Runtime
显示：
当前 project_root
当前 TestRun
当前 Vehicle
当前 sensor
CarMaker 状态
GUI Movie 状态
DDE 状态

按钮：
探测运行态
准备运行环境
打开或复用 CarMaker
打开或复用 GUI Movie
停止当前仿真

2. 中栏：Calibration
输入：
配置文件选择
TestRun 选择
camera sensor 选择
campaign rounds
是否 open movie
是否 keep CarMaker open
是否 keep movie open

按钮：
开始标定
停止标定
仅做 smoke test
打开输出目录

3. 右栏：Output
显示：
实时日志
当前输出目录
最新 best score
最新 best values 摘要
最近一次错误信息

**关键交互流程**
第一阶段只实现 3 条主流程。

1. 运行态探测
点击后执行：
进程探测
DDE 健康检查
读取当前 CarMaker projectdir
读取当前 TestRun
读取当前 Vehicle
读取 GUI Movie 进程信息

成功后界面刷新状态灯和文本。

2. Prepare Runtime
点击后执行：
校验 project_root
按规则复用或重启 CarMaker
按规则复用或启动 GUI Movie
解析 TestRun 对应 Vehicle
切换目标单 camera sensor

这一步本质上是对 cmapi_testrun_control.py 的一层图形封装。

3. Start Calibration
点击后执行：
锁定当前配置
生成本次 run id
启动 camera_calibration.py 子进程
实时读取日志
任务结束后自动刷新 result panel

**底层调用策略**
这是最关键的实现选择。

第一阶段不要把现有脚本改成“大量可 import service API”，先用子进程适配。

建议这样：

1. Runtime 相关
直接调用 cmapi_testrun_control.py
但补两个轻量 CLI 模式：
status
prepare

status 负责只读探测，不启动仿真
prepare 负责按规则把环境整理到可运行状态

2. Calibration 相关
直接调用 camera_calibration.py
例如：
选择 config
传 explore-then-refine
传 campaign-rounds
把 stdout/stderr 全部流式回 GUI

这样 GUI 只是 orchestration shell，不碰你现有优化逻辑。

**建议补充的 CLI 能力**
为了让 GUI 层更干净，我建议给现有脚本补少量面向 GUI 的出口，而不是让 GUI 自己拼很多 PowerShell。

优先补这几个。

1. cmapi_testrun_control.py 增加 status 模式
输出 JSON，至少包含：
project_root
running_projectdir
testrun
vehicle
carmaker_pid
movie_pid
gui_movie_count
gpusensor_count
reuse_ok
errors

2. cmapi_testrun_control.py 增加 prepare 模式
执行：
projectdir 校验
CarMaker 复用或重启
GUI Movie 复用或重启
sensor 激活
最后输出 JSON 摘要

3. camera_calibration.py 增加可选 machine readable summary
任务结束时除正常日志外，再输出一行 JSON summary，便于 GUI 抓取：
status
output_dir
best_score
result_json
campaign_summary_json

这样 GUI 解析会稳定很多。

**状态机**
建议 GUI 内部显式维护状态，不要靠按钮点到哪算哪。

最少需要这几个状态：

1. idle
未探测

2. runtime_unknown
未确认当前 CarMaker 工程

3. runtime_ready
CarMaker 和 GUI Movie 满足预期，sensor 已就位

4. calibration_running
标定任务运行中

5. calibration_finished
任务正常结束

6. calibration_failed
任务失败

按钮启停逻辑跟状态绑定，不要写成散乱 if。

**日志与结果**
建议把日志分成两类：

1. 任务日志
直接来自子进程 stdout/stderr，实时显示

2. 结构化摘要
由 GUI 每次任务结束后写一份自己的 run meta，例如：
开始时间
结束时间
config
testrun
vehicle
sensor
output_dir
exit_code
best_score

这个摘要单独存到一个 gui_runs.jsonl 就够了，便于以后补历史视图。

**错误处理**
这类本地编排最容易烂在错误处理上，所以第一版就要明确分层。

1. 环境错误
例如 cmapi 不可导入、DDE 不通、CarMaker 不在线

2. 运行态不匹配
例如 projectdir 不对、多个 CarMaker、多个 GUI Movie

3. 任务执行错误
例如 camera_calibration.py 退出码非 0

4. 结果解析错误
例如 result.json 不存在或不完整

每类错误都要在 GUI 里单独提示，不要只把异常堆到日志窗口。

**为什么先不做本地 web**
如果按这个蓝图做，exe 版本能直接落地，而且复用现有脚本最多。
如果改成本地 web，你还得再多做这些：

1. 本地 HTTP 服务
2. 前后端协议
3. 浏览器状态同步
4. 长任务轮询或 websocket
5. 端口冲突和启动顺序

这些工作对当前目标没有收益，只会推迟你拿到可用工具的时间。

**第一阶段里程碑**
我建议按 4 个里程碑做。

1. M1 运行态面板
完成：
探测当前工程、TestRun、Vehicle、sensor、CarMaker、Movie
只读显示
不启动标定

2. M2 运行准备
完成：
prepare runtime
按规则复用或重启
单 sensor 激活
GUI 状态刷新

3. M3 标定任务托管
完成：
从 GUI 启动 camera_calibration.py
实时日志
停止任务
结束后读取 best result

4. M4 体验收口
完成：
配置文件选择器
最近使用项
输出目录快捷打开
错误弹窗和状态灯

**第二阶段可选项**
等第一阶段稳定后，再考虑这些：

1. 历史结果列表
2. 参数 diff 视图
3. best image 预览
4. campaign rounds 结果趋势图
5. 本地 web dashboard，只读展示历史结果

这里注意，第二阶段的 web 如果要加，最好只做展示层，不接管控制层。控制仍然走现有 Python orchestrator。

**最小开工顺序**
如果现在就开始做，我建议第一刀这样下：

1. 新建 gui_app 目录
2. 用 PySide6 拉起一个主窗口
3. 先做 Runtime 面板
4. Runtime 面板只接一个按钮：探测运行态
5. 这个按钮先通过子进程调用现有脚本或轻量 helper
6. 读到 projectdir、TestRun、Vehicle 后再往下扩

也就是说，第一周不要碰“开始标定”按钮，先把运行态探测和准备做稳。