# CameraCalibration 使用流程指南

> 创建日期：2026-06-18
> 状态：✅ ACTIVE
> 对应版本：v1.1+

---

## 一、环境准备

### 1.1 硬件与软件要求

| 项目 | 要求 |
|------|------|
| CarMaker | 14.1+（win64） |
| IPG-MOVIE | 与 CarMaker 匹配版本 |
| Python | 3.10+（64位） |
| 操作系统 | Windows 10/11 64位 |
| 显卡 | 支持 OpenGL 3.3+ |
| 内存 | 16GB+（建议 32GB） |

### 1.2 Python 依赖

```bash
pip install -r project_notes/requirements.txt
```

关键依赖：
- `opencv-python` ≥ 4.9 — 图像处理、标定板检测
- `PySide6` ≥ 6.6 — GUI 界面
- `numpy`, `scipy` — 数值计算
- `optuna` — 超参优化（可选，用于 multi-start 模式）
- `rapidocr-onnxruntime` — 标注板 OCR 识别（可选）

### 1.3 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `IPGHOME` | CarMaker 安装根目录 | `D:/IPG` |
| `PYTHONPATH` | 需包含项目根目录和 cmapi | 由 launch 脚本自动设置 |

### 1.4 项目结构

```
CameraCalibration/
├── camera_calibration.py        # 核心标定算法 (~3200行)
├── calibration_orchestrator.py  # 多相机编排 (# ~712行)
├── cmapi_testrun_control.py     # CarMaker/Movie 控制 (~3000行)
├── launch_gui.py                # GUI 启动入口
├── launch_wizard.py             # Wizard 独立启动入口
├── gui_app/                     # GUI 前端
│   ├── app.py                   # Qt 应用
│   ├── main_window.py           # 主窗口
│   ├── services/                # 后台服务
│   └── widgets/                 # UI 组件
├── configs/                     # 相机标定配置
├── project_notes/               # 文档
└── tests/                       # 测试
```

---

## 二、操作流程

### 2.1 启动 GUI

```bash
python launch_gui.py
```

启动后窗口分为左右两栏：
- **左侧**：项目设置 → 标定控制 → 相机进度
- **右侧**：日志输出面板

### 2.2 配置项目

1. **选择项目目录**（CmSettingsPanel）
   - 点击 "Project" 按钮选择 CarMaker 项目根目录
   - 项目目录应包含 `Vehicle/`, `TestRun/`, `Movie/` 等标准子目录

2. **选择 TestRun**
   - 从下拉列表选择目标 TestRun
   - 系统自动读取车辆文件中的传感器列表

3. **选择传感器（相机）**
   - 从车辆传感器列表中勾选需要标定的相机
   - 每行显示传感器名称、传感器索引、支持状态

### 2.3 预检（自动执行）

点击 "Start Calibration" 后，系统自动执行预检：

```
┌─ Precheck ──────────────────────────────────┐
│ ✅ mapping 文件存在                          │
│ ✅ 每个相机有配置                            │
│ ✅ 标定板定义有效                            │
│ ✅ 参考图像路径有效                          │
│ ✅ Wizard 已生成配置                         │
└─────────────────────────────────────────────┘
```

预检通过后自动进入编排流程。预检失败则显示具体错误原因。

### 2.4 Wizard — 标定板配置

首次标定新相机时需要先运行 Wizard：

**方式一**：从 GUI 面板点击相机行的 "Wizard" 按钮
**方式二**：`python launch_wizard.py`

Wizard 共 3 页：

#### 第1页 — 输入
- 选择相机传感器
- 选择参考图像（标定板照片）
- 可选择标注图像（带矩形标注框的版本）
- 选择板类型：

| 板类型 | 说明 | 适用场景 |
|--------|------|---------|
| Checkerboard | 标准棋盘格 | 最常见，检测稳定 |
| ArUco | ArUco 标记阵列 | 部分遮挡场景 |
| AprilTag | AprilTag 标记 | 高精度 |
| CharUco | Checkerboard + ArUco 混合 | 通用 |
| Circle Grid | 圆点阵列 | 鱼眼标定 |
| ArUco Grid | ArUco 规则网格 | 灵活配置 |
| Custom | 手动绘制任意板 | 特殊场景 |

#### 第2页 — 检测与审查
- 自动检测标定板并显示预览
- 支持缩放、拖拽查看
- 表格列出所有检测到的板，可删除/禁用/编辑ID
- 确认检测结果正确后进入下一步

#### 第3页 — 生成
- 预览生成的 JSON 配置
- 选择 Bootstrap 模板
- 确认输出目录
- 点击 "Generate" 生成 `camera.<cam_name>.json` 配置
- 系统自动更新 `calibtool_camera_config.json` 映射

### 2.5 启动标定

1. 确认所有目标相机已通过 Wizard 配置
2. 在 CalibrationPanel 中设置标定参数：
   - **Strategy**：优化策略（rounds / explore-then-refine 等）
   - **Rounds**：优化轮数
   - **Starts**：multi-start 并行起跑数（默认3）
   - **Max Iters**：每轮最大迭代次数
   - **Jitter Steps**：参数扰动步长
3. 点击 "Start Calibration" 开始

### 2.6 标定流程

启动后，编排器按以下流程执行：

```
┌─ Calibration Flow ─────────────────────────────────────┐
│                                                         │
│  [Cleanup]  CarMaker/Movie 清理                          │
│      ↓                                                   │
│  [Bootstrap] 运行时配置生成                               │
│      ↓                                                   │
│  ┌─ 对每个相机循环 ──────────────────────────┐           │
│  │                                             │           │
│  │  1. Prepare Runtime:                        │           │
│  │     a. 启动 CarMaker (HIL.exe)             │           │
│  │     b. 激活目标传感器（唯一激活）           │           │
│  │     c. 同步 TestRun 选择                    │           │
│  │     d. StartSim / StopSim 引导              │           │
│  │     e. 启动 GUI Movie                       │           │
│  │     f. 设置视图尺寸 + ABRAXAS               │           │
│  │     g. 选择相机视角                         │           │
│  │     h. 捕获传感器初始值                     │           │
│  │     i. 健康检查（DDE 8项探针 + FBO）        │           │
│  │     ↓                                       │           │
│  │  2. Run Calibration:                        │           │
│  │     ┌─ Multi-Start 循环 ──────────┐         │           │
│  │     │ 每次:                        │         │           │
│  │     │ 调整参数 → DDE写入CarMaker   │         │           │
│  │     │ → FBO抓图 → 检测板 → 评分    │         │           │
│  │     │ → 接受/回滚 → 继续迭代       │         │           │
│  │     └─────────────────────────────┘         │           │
│  │     ↓                                       │           │
│  │  3. Write Results:                          │           │
│  │     a. 写入 result.json                     │           │
│  │     b. 更新参数历史池                       │           │
│  │     c. 最佳结果写回 Vehicle 配置文件        │           │
│  │     d. 生成标定报告                         │           │
│  │                                             │           │
│  └─────────────────────────────────────────────┘           │
│      ↓                                                   │
│  [Summary]  写入 task_summary.json                         │
│              发出 ORCHESTRATION_SUMMARY_JSON               │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 2.7 查看结果

标定过程中，GUI 面板实时显示：
- 当前相机名称
- 当前迭代次数 / 总迭代数
- 最佳分数变化曲线
- 日志输出

标定完成后：
- 每个相机生成独立结果目录（`SimOutput/calibration/<camera>/`）
- 每轮结果含：`result.json`、分数趋势图、最佳覆盖图
- 最终参数自动写入 Vehicle 配置文件
- 编排器写 `task_summary.json` 汇总

---

## 三、标定策略说明

### 3.1 Rounds（轮次优化）

每一轮使用不同的初始参数分布（基于上一轮最佳结果 + 扰动），独立优化后选出最佳。

```
Round 1:  探索搜索空间（大扰动）
Round 2+: 精炼优化（小扰动）
```

### 3.2 Multi-Start（多起跑）

每次启动使用不同的随机种子，独立进行优化，最后取所有 start 中最佳结果。

- 默认 3 个 start
- 有效避免局部最优
- 每个 start 在子目录 `start_00/`, `start_01/`, `start_02/` 下

### 3.3 Explore-Then-Refine

先执行探索轮（高扰动、宽搜索），再执行精炼轮（低扰动、收敛）。

- `explore_iters`：探索迭代次数
- 探索阶段参数跨大步搜索
- 精炼阶段在最佳区域小步收敛

### 3.4 优化策略要素

详见 `strategy-adaptation.md`：

| 策略 | 说明 |
|------|------|
| `round_seeding` | 轮次种子从上一轮最佳分布采样 |
| `isolated_board_guard` | 检测到孤立板时保护性回滚 |
| `escape_exploration` | 陷入局部最优时跳跃式探索 |
| `objective_board_focus` | 只对特定板计算分数 |

---

## 四、标定结果解释

### 4.1 分数（Score）

分数越低越好。理想值为 0。

| 分数区间 | 含义 |
|---------|------|
| 0.0 - 1.0 | 优秀，投影误差在子像素级 |
| 1.0 - 3.0 | 良好，肉眼几乎不可见偏差 |
| 3.0 - 5.0 | 可接受，需人工确认 |
| > 5.0 | 差，需重新标定或检查标定板 |

### 4.2 输出文件

每个相机在 `SimOutput/calibration/<camera>/` 下生成：

| 文件 | 说明 |
|------|------|
| `result.json` | 最终结果（参数、分数、接受信息） |
| `run.log` | 完整运行日志 |
| `camera_summary.json` | 相机历史摘要 |
| `historical_params_pool.json` | 历史参数池（防回退） |
| `wizard_preview_*.png` | 最佳分数标注图 |
| `best_overlay_*.png` | 最佳叠加图 |

### 4.3 车辆回写

标定完成后，最佳参数会自动写入 Vehicle 配置文件的对应传感器：

```
Vehicle/<vehicle_name>.xml:
  Sensor/<sensor_name>:
    Pos: <标定后的位置>
    Rot: <标定后的旋转>
    FoV: <标定后的视场角>
```

回写受历史参数池保护：仅当本次结果明显优于历史记录时才写入。

---

## 五、常见问题

### 5.1 CarMaker 不启动

- 检查 `IPGHOME` 环境变量是否正确
- 检查是否有残留进程：`taskkill /IM CarMaker.win64.exe /F /T`
- 检查 License 是否可用

### 5.2 Movie 黑屏/无法渲染

- 检查显卡驱动是否为最新
- 检查 ABRAXAS 模式是否启用（`ensure_movie_abraxas_enabled`）
- 检查视图尺寸是否与参考图像匹配
- FBO 健康检查失败时会自动杀进程重试一次

### 5.3 标定启动后无响应（9分钟）

- WMI 服务卡死 → 重启 `Winmgmt` 服务
  ```cmd
  net stop winmgmt
  net start winmgmt
  ```
- 或重建 WMI 仓库：`winmgmt /salvagerepository`
- 已加代码保护：进程枚举 5s 超时 + taskkill 3s 超时

### 5.4 OpenCV OOM（内存不足）

- 检查系统内存使用情况
- 关闭其他占用内存的应用
- 页面文件设置为 16GB+
- 如持续出现，在 `camera_calibration.py` 中检查图像加载路径

### 5.5 标定分数不收敛

- 检查标定板是否清晰可见
- 确认标定板类型选择正确
- 增大 rounds 或 max_iters
- 尝试不同的 initial_values
- 检查 Movie 视图尺寸是否与参考图像一致

---

## 六、CLI 命令参考

### 启动 GUI
```bash
python launch_gui.py
```

### 独立启动 Wizard
```bash
python launch_wizard.py
```

### 手动运行预检
```bash
python precheck_cli.py --project <path> --camera <camera_name> [--camera ...]
```

### 直接运行编排器
```bash
python calibration_orchestrator.py --camera TRight --camera front_wide
```

### DDE 健康检查
```bash
python dde_health_check.py [--timeout 30]
```

### 对标定结果评分
```bash
python fbo_score_check.py --config <config.json> --camera <camera_name>
```

---

## 七、开发指引

### 测试运行

```bash
pytest tests/ -v
```

### 增加新相机类型

1. 在 Vehicle 文件中配置传感器
2. 运行 Wizard 生成配置
3. 通过 GUI 勾选并启动标定

### 增加新标定板类型

1. 在 `board_auto_detector.py` 中添加检测器
2. 在 Wizard 的板类型下拉列表中添加
3. 在 `bootstrap.template.json` 中添加模板定义

---

## 附录：文件速查

| 文件 | 作用 | 调用者 |
|------|------|--------|
| `launch_gui.py` | GUI 入口 | 用户 |
| `gui_app/main_window.py` | 主窗口 | launch_gui |
| `gui_app/services/calibration_service.py` | 标定子进程管理 | main_window |
| `gui_app/services/process_service.py` | QProcess + JSON 协议 | services |
| `gui_app/widgets/bootstrap_wizard.py` | 3页 Wizard | main_window, launch_wizard |
| `calibration_orchestrator.py` | 多相机编排 | calibration_service |
| `cmapi_testrun_control.py` | CarMaker/Movie 控制 | orchestrator |
| `camera_calibration.py` | 核心标定算法 | orchestrator |
| `dde_health_check.py` | DDE 健康检查 | orchestrator |
| `runtime_config_bootstrap.py` | 运行时配置生成 | orchestrator |
| `precheck_cli.py` | 预检 | calibration_service |
| `fbo_score_check.py` | FBO 抓图+评分 | camera_calibration |
