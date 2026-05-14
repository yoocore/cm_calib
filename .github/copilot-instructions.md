# CameraCalibration 仓库协作说明

## EXE 交付硬约束

- 这个项目的 GUI 交付目标必须是 **portable EXE 包**，用户拿到后应能直接运行，**不能要求用户额外安装 Python、pip、虚拟环境或手工准备运行时依赖**。
- 仓库里的 Python 命令、pytest 命令、`.venv` 解释器路径，都是给开发和维护阶段使用的，不是最终用户的使用前提。
- 以后如果修改 GUI 启动链、增加依赖、拆分资源文件或新增辅助脚本，优先考虑这些内容能否随 EXE 一起打包分发；不要把“先装环境再运行”当成默认方案。
- 与 GUI 相关的实现、目录组织、资源引用和子进程调用方式，都应尽量服务于 portable 打包，而不是依赖用户机器上的预装 Python 环境。

## 常用命令

- 下面这些命令主要用于开发、调试、测试和回归，不代表最终用户需要这样使用系统。
- 安装当前维护的运行时依赖：`python -m pip install -r project_notes\requirements.txt`
- 如果存在项目本地虚拟环境，优先使用：`C:\CM_Projects\CMO141_Calibration\.venv\Scripts\python.exe`
- 启动桌面 GUI：`python launch_gui.py`
- 构建 portable GUI 分发包：`powershell -ExecutionPolicy Bypass -File .\build_portable_gui.ps1`
- 运行独立 precheck CLI：`python precheck_cli.py --project-root <CarMaker项目根目录> --camera rear_tv`
- 运行 `camera_calibration.py` 的 precheck 模式：`python camera_calibration.py --precheck --project-root <CarMaker项目根目录> --camera rear_tv`
- 运行单相机标定：`python camera_calibration.py --config configs\camera.rear_tv.json`
- 将当前 Script Control 读取到的参数回写到配置：`python camera_calibration.py --config configs\camera.rear_tv.json --capture-initials --write-initials-to-config`
- 生成某个新相机的配置：`python camera_calibration.py --bootstrap-config-from-annotation --bootstrap-template-config configs\bootstrap.template.json --bootstrap-real-image <real-image> --bootstrap-annotated-image <annotated-image>`
- 对 bootstrap 结果做健康检查：`python bootstrap_template_health_check.py --config configs\camera.right_rear.json`
- 运行 GUI 同款 prepare 流程：`python cmapi_testrun_control.py --mode prepare --project-root <CarMaker项目根目录> --testrun <相对Data\TestRun的路径> --camera-sensor <sensor> --print-summary-json`
- 运行冻结运行链验证脚本：`python verify_runtime_chain_baseline.py --testrun <相对Data\TestRun的路径> --print-summary-json`
- GUI 测试总入口：`python -m pytest gui_app\tests -q`
- 单个 GUI 测试示例：`python -m pytest gui_app\tests\test_runtime_service.py::TestRuntimeService::test_prepare_runtime_passes_cm_install -q`
- 另一个常用单测节点：`python -m pytest gui_app\tests\test_calib_start_flow.py::TestCalibStartFlow::test_runtime_summary_prepare_ready_triggers_calibration_start -q`
- 当前提交的 `project_notes\requirements.txt` 只覆盖运行时依赖，不包含 pytest 相关工具；跑 `gui_app\tests` 前需要自行在本地环境补齐 `pytest`、`pytest-qt`、`pytest-mock`
- `gui_app\tests` 以临时 project_root 搭最小 CarMaker 目录树来测 GUI / service 逻辑，重点是状态流和参数拼装，不是实际 DDE 链路联调

## 高层架构

- 面向用户的目标形态是一个本地单机的 portable GUI 控制台；当前仓库里的 Python CLI 和 `gui_app\` 是这个 EXE 的实现基础，而不是要求用户直接操作的一组源码脚本。
- `camera_calibration.py` 是单相机标定主链，也是单相机能力的唯一真实入口。它负责 precheck、基于标注图的 bootstrap 配置生成、boards 提议、优化迭代、结果落盘，以及 `CALIBRATION_PROGRESS_JSON:` / `CALIBRATION_SUMMARY_JSON:` 结构化输出。
- `calibration_orchestrator.py` 是多相机顺序编排层。它会通过 `cmapi_testrun_control.py` 切换 active sensor、准备 CarMaker 与 IPG-MOVIE 运行态、等待 Movie 场景 ready、按需做健康检查，然后逐个调用 `camera_calibration.py`。
- `cmapi_testrun_control.py`、`verify_runtime_chain_baseline.py`、`dde_health_check.py`、`ipgmovie_health_monitor.py` 组成运行态控制层，负责 CarMaker / IPG-MOVIE 生命周期、DDE 探测、冻结链路验证。新增功能应优先复用这些 CLI，而不是在别处重写一套运行态逻辑。
- `gui_app\` 只是对现有 CLI 的一层 PySide6 薄封装。`MainWindow` 维护状态机，`RuntimeService` 和 `CalibrationService` 用 `QProcess` 拉起后端脚本，`PrecheckService` 通过 `precheck_cli.py` 和 bootstrap helper 完成前置检查与配置生成。
- `ProcessService` 是 GUI 与后端脚本之间的协议层：它持续读取 stdout/stderr，并按前缀解析结构化 JSON 消息来刷新界面状态。
- portable 打包场景下，GUI 子进程不再假设 `sys.executable` 一定是 `python.exe`；冻结后的 GUI 会通过同一个 EXE 加 `--camcal-dispatch` 去转发 `cmapi_testrun_control.py`、`precheck_cli.py`、`calibration_orchestrator.py` 这类后端脚本。
- GUI 的主流程是三级串联：`PrecheckService.run_for_cameras()` 先验输入，`RuntimeService.prepare_runtime()` 调 `cmapi_testrun_control.py` 做 prepare，收到 `status=ready` 的运行态摘要后，再由 `CalibrationService` 启动 `calibration_orchestrator.py`。
- `calibration_orchestrator.py` 自身不做单相机算法，它负责“切 sensor → prepare runtime → 启动单相机脚本 → 汇总每相机结果”的任务级生命周期，并通过 `ORCHESTRATION_EVENT_JSON:` / `ORCHESTRATION_SUMMARY_JSON:` 把 task 级进度回推给 GUI。
- `runtime_config_bootstrap.py` 是 GUI 和 CLI 共用的配置生成辅助层：它会在 `<project_root>\Movie` 中按相机名查找原图与标注图，必要时把已有配置备份成 `.prepare.<timestamp>.bak.json`，然后调用 `camera_calibration.bootstrap_config_from_annotation(...)` 产出新的 `camera.<camera>.json`。
- `precheck_cli.py` 是一个很轻量的前端友好检查器：它只检查 Movie 资产、bootstrap 模板、现有 config/backup 是否存在，并输出 `PRECHECK_RESULT_JSON:` 供 GUI 的 precheck 树直接消费。
- GUI 三个面板的职责是固定的：`RuntimePanel` 负责 project/TestRun/Vehicle/sensor 展示，`CalibrationPanel` 负责 camera 顺序、rounds/explore/refine/CM 版本选择以及失败摘要，`OutputPanel` 负责实时日志、每相机结果卡片和产物预览。

## 关键约定

- 默认运行环境是 Windows，并且代码假设自己处在完整 CarMaker 工程内。代码里提到的 `project_root` 指的是当前仓库上一级的 CarMaker 项目根目录，标定目录会按 `<project_root>\Data\Script\CameraCalibration` 解析。
- 每个视角只保留一份版本化运行配置，命名为 `configs\camera.<camera>.json`。以 `.bak.json` 结尾的是备份文件，`ConfigService` 会主动忽略。
- 调 `camera_calibration.py` 时始终显式传 `--config`。本仓库已经有意放弃 `best` / `final` / `overnight` 之类的多变体版本化配置工作流。
- 当前维护中的有效运行链是：Script Control DDE 负责写参，IPG-MOVIE FBO 负责抓图。不要在 GUI 或新脚本里重新引入其它窗口连接或抓图路径作为主链。
- `script_control_apply.tcl` 是当前唯一维护的 Script Control Tcl 入口。
- 标准输出里的结构化前缀本身就是接口协议，修改 CLI 输出时必须保持这些前缀兼容：`PRECHECK_RESULT_JSON:`、`CMAPI_CONTROL_SUMMARY_JSON:`、`ORCHESTRATION_EVENT_JSON:`、`ORCHESTRATION_SUMMARY_JSON:`、`CALIBRATION_PROGRESS_JSON:`、`CALIBRATION_SUMMARY_JSON:`
- GUI 路径仍要兼容 Python 3.10：`launch_gui.py` 和 `gui_app\tests\conftest.py` 都会通过 `strenum` 给 `enum.StrEnum` 打补丁
- 与项目强相关、需要长期查阅的知识文档集中在 `project_notes\`，尤其是 `README.md`、`design.md`、`spec.md`、`gui-control-blueprint.md`、`ipgmovie_control_workflow.md`
- precheck 和配置生成并不只依赖当前仓库子目录，还依赖 `<project_root>\Movie`、`<project_root>\Data\TestRun`、`<project_root>\Data\Vehicle` 下的外部工程资产
- `RuntimeService` / `CalibrationService` 都会把脚本工作目录固定到 `Data\Script\CameraCalibration`，并在指定 CM 安装目录后动态拼接 `PYTHONPATH` 去接 CarMaker 自带 Python 包；如果你改启动方式，不要破坏这条约束
- 运行态判定里，`HIL.exe` 应视为 CarMaker GUI 前端，`CarMaker.win64.exe` / `CM_Office.exe` 应视为 backend runtime；正常 prepare 后同时存在 GUI 前端和 1 个 backend runtime 不应再被判定为重复运行态
- CarMaker Python API 不能再只按旧的 `Python\Lib\...` 布局假设；当前机器上的有效布局是 `<cm_install>\Python\python3.10` / `python3.12` 这种版本化目录，portable 相关代码必须优先兼容这种结构
- 如果后续增加打包配置、启动器或资源目录，默认目标是“让最终 EXE 包自带并定位这些依赖”；不要把依赖解析建立在用户手工配置环境变量、安装 Python 包或创建虚拟环境之上
