# CameraCalibration 技术原理综述

> 创建日期：2026-06-18
> 状态：✅ ACTIVE
> 对应版本：v1.1+

---

## 一、项目概述

### 1.1 目标

在 CarMaker/IPG-MOVIE 仿真环境中，通过自动化迭代优化，将虚拟摄像头的安装参数（位置、旋转、视场角）微调到与真实参考图像中的标定板特征对齐，实现车载摄像头的**半自动标定**。

### 1.2 核心挑战

| 挑战 | 解决方案 |
|------|---------|
| 仿真参数与真实图像差异不可知 | 迭代优化（Optuna + 贪心搜索） |
| CarMaker/Movie 运行态复杂 | 编排器 + DDE/FBO 控制链 |
| 多相机并行标定 | 顺序编排 + 运行时复用 |
| 渲染/通信故障频发 | 健康检查 + 自动恢复 |
| 标定结果防回退 | 历史参数池 + 重评估保护 |

### 1.3 系统边界

```
┌── 输入 ──────────────────────────────────────┐
│  • 参考图像（实车拍摄的标定板照片）             │
│  • 标注图像（可选，带矩形标注框）               │
│  • Vehicle 配置文件（当前传感器参数）           │
│  • TestRun 配置（相机选择）                    │
├── 内部 ──────────────────────────────────────┤
│  • 仿真渲染（IPG-MOVIE）                       │
│  • 标定板检测（OpenCV）                        │
│  • 参数优化（迭代搜索）                        │
├── 输出 ──────────────────────────────────────┤
│  • 标定后参数（写回 Vehicle 文件）              │
│  • 标定报告（result.json + 趋势图）            │
│  • 参数历史池（跨次运行复用）                   │
└──────────────────────────────────────────────┘
```

---

## 二、系统架构

### 2.1 三层架构

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: 用户界面层 (GUI)                                    │
│  launch_gui.py → app.py → MainWindow                        │
│  ├─ 项目/TestRun/相机选择（CmSettingsPanel）                  │
│  ├─ 标定控制面板（CalibrationPanel）                          │
│  ├─ Wizard 对话框（BootstrapWizardDialog）                    │
│  ├─ 日志/结果展示（OutputPanel + SensorProgressPanel）         │
│  └─ 进程通信（ProcessService + 前缀JSON 协议）                │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: 服务层 (Services)                                   │
│  ├─ ConfigService     — JSON 配置发现/解析                    │
│  ├─ PrecheckService   — 启动前预检                           │
│  ├─ RuntimeService    — 运行时环境准备                       │
│  ├─ CalibrationService — 标定子进程启停                      │
│  └─ ProcessService    — QProcess 封装 + 结构化通信            │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: 编排与执行层                                        │
│  ├─ calibration_orchestrator.py  — 多相机顺序编排            │
│  ├─ cmapi_testrun_control.py     — CarMaker/Movie 生命周期   │
│  ├─ camera_calibration.py        — 核心标定算法              │
│  ├─ dde_health_check.py          — DDE/FBO 健康检查          │
│  └─ runtime_config_bootstrap.py  — 运行时配置生成            │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 进程模型

```
┌─ 主进程（GUI） ──────────────────────────────────┐
│  MainWindow (QMainWindow)                         │
│  ├─ ProcessService (QProcess)                     │
│  │   └─ stdout 监听 → 前缀行解析                  │
│  └─ 子进程状态管理                                │
└───────────┬───────────────────────────────────────┘
            │ python calibration_orchestrator.py ...
            ▼
┌─ 编排子进程 ──────────────────────────────────────┐
│  calibration_orchestrator.main()                   │
│  ├─ kill_existing_cm_processes()                  │
│  ├─ bootstrap_runtime_configs_for_cameras()       │
│  ├─ _prepare_runtime_for_camera() → DDE + Movie  │
│  └─ _run_single_camera_process()                  │
│       └─ 子进程: python camera_calibration.py    │
│           （核心标定算法）                          │
└──────────────────────────────────────────────────┘
```

关键决策：**子进程架构而非线程**。标定可能运行数小时，子进程崩溃不影响 GUI 响应。

### 2.3 通信协议：前缀JSON

GUI 与子进程间的结构化通信：

```
ORCHESTRATION_EVENT_JSON:{"type":"task_started","camera_names":[...]}
CALIBRATION_PROGRESS_JSON:{"iter":5,"best_score":2.34,...}
ORCHESTRATION_SUMMARY_JSON:{"status":"finished","results":[...]}
CMAPI_CONTROL_SUMMARY_JSON:{"processes":[...],"health":{...}}
PRECHECK_RESULT_JSON:{"ok":true,"results":[...]}
```

ProcessService 逐行解析 stdout，根据前缀分发到不同信号（signal-slot），MainWindow 订阅对应信号更新 UI。

---

## 三、标定编排流程

### 3.1 完整数据流

```
[用户点击 Start Calibration]
    │
    ▼
1. Build Launch Config ───────────────── GUI 读取面板参数
    │  - 项目目录、TestRun、相机列表
    │  - 策略（rounds/explore-then-refine）、参数
    │
    ▼
2. Precheck ──────────────────────────── precheck_cli.py
    │  - mapping 文件存在性
    │  - 每个相机的配置完整性
    │  - 标定板定义、参考图像路径
    │
    ▼
3. Kill Stale Processes ──────────────── cmapi_testrun_control
    │  - taskkill /IM (CarMaker/Movie)
    │  - 超时保护 3s/5s
    │
    ▼
4. Bootstrap Runtime Configs ─────────── runtime_config_bootstrap
    │  - 从映射文件查找配置路径
    │  - 或用 annotation 自动生成配置
    │
    ▼
5. ┌─ Camera Loop ────────────────────── calibration_orchestrator
    │   │
    │   ▼
    │  5a. Prepare Runtime ───────────── cmapi_testrun_control
    │  │  - 启动 CarMaker (HIL.exe)
    │  │  - 激活传感器（唯一激活）
    │  │  - 同步 TestRun
    │  │  - StartSim/StopSim 引导
    │  │  - 启动 GUI Movie
    │  │  - 设置视图尺寸
    │  │  - 启用 ABRAXAS
    │  │  - 选择相机视角
    │  │  - 捕获初始值
    │  │  - DDE 健康检查 (8项探针)
    │  │  - FBO 健康检查
    │  │
    │   │  若 FBO 损坏 → kill_all → 重试一次
    │  │
    │  ▼
    │  5b. Run Calibration ───────────── camera_calibration
    │      - Multi-start 并行/顺序优化
    │      - 每步：调参 → DDE写入 → FBO抓图 → 检测 → 评分
    │      - 最佳结果写回 Vehicle 文件
    │
    │  (对下一个相机重复 5a-5b)
    │
    ▼
6. Write Task Summary ────────────────── task_summary.json
```

### 3.2 Prepare Runtime 详解

Prepare 是整个流程最复杂的部分，目标是为标定建立干净的运行环境：

```
主要步骤（cmapi_testrun_control.py）：
 1. start_carmaker_via_tcl()
      → DDE "StartCarMaker" → 等待 TclEval 就绪
 2. activate_single_vehicle_sensor()
      → 修改 Vehicle XML，目标传感器 Active=1，其余=0
      → 使 Movie 只渲染目标相机
 3. sync_gui_testrun_selection()
      → DDE TclEval "CarMaker GUI SelectTestRun"
      → 确保 CarMaker GUI 加载了正确 TestRun
 4. bootstrap_testrun_for_movie_via_cmapi()
      → StartSim → 等待 → StopSim
      → 让 Movie 完成初始加载
 5. start_gui_movie_process()
      → 启动 Movie.exe -project ... -cmgui
      → 等待 DDE 响应 + 场景就绪
 6. ensure_movie_view_size()
      → DDE View::SetSize(width, height)
      → 匹配参考图像分辨率
 7. ensure_movie_abraxas_enabled()
      → DDE "abraxas enable"
      → 设置固定场景参数（时间、天气等）
 8. ensure_movie_camera_selected()
      → DDE 选择目标相机的渲染视图
 9. capture_initial_values_to_config()
      → DDE 读取当前传感器参数作为起始点
 10. dde_health_check.run_check_attempt()
      → 8项探针验证所有通信链路
```

### 3.3 自动恢复机制

| 故障类型 | 检测方式 | 恢复行为 |
|---------|---------|---------|
| 渲染冻结 | `check_movie_fbo()` 超时 | `kill_all_processes()` + 重试 prepare |
| FBO 损坏 | FBO 创建/删除失败 | 同上，最多重试1次 |
| DDE 不响应 | 健康检查 probe 超时 | 报错终止（需人工检查 CarMaker） |
| WMI 卡死 | `_run_powershell_json` 5s超时 | 返回空进程列表，taskkill 按名称杀 |

---

## 四、核心算法详解

### 4.1 标定参数

优化的参数集（来自 Vehicle 文件传感器定义）：

| 参数 | 含义 | 自由度 |
|------|------|--------|
| Pos.X/Y/Z | 传感器位置（米） | 3 |
| Rot.Roll/Pitch/Yaw | 传感器旋转（度） | 3 |
| FoV.Horizontal/Vertical | 视场角（度） | 2 |
| Lens 参数 | 畸变系数（如有） | 2-4 |

默认共优化 **8-10 个参数**。

### 4.2 迭代优化流程

```
┌─ Single Iteration ─────────────────────────────────┐
│                                                      │
│  1. 采样新参数（基于当前最优 + 扰动）                 │
│      ↓                                               │
│  2. DDE Script Control 写入 CarMaker                 │
│      script_control_apply.tcl → SetValue             │
│      ↓                                               │
│  3. FBO 抓图 (capture IPG-MOVIE viewport)            │
│      ↓                                               │
│  4. OpenCV 检测标定板                                 │
│      ↓                                               │
│  5. 计算投影误差分数                                   │
│      ↓                                               │
│  6. 判断接受/回滚:                                    │
│      - 分数提升 → 接受，更新最优                      │
│      - 分数下降 → 回滚到上一组参数                    │
│      - 孤立板 → 保护性回滚                            │
│      ↓                                               │
│  7. 报告进度 JSON 行到 stdout                         │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 4.3 优化策略

#### Round Seeding（轮次播种）

每轮开始时，初始参数从**上一轮最佳参数分布**中采样，而非固定值。

```
Round 1: 初始值 = Vehicle 当前值 + N(0, σ₁)
          → 搜索空间探索
Round 2: 初始值 = Round 1 最佳 + N(0, σ₂)  [σ₂ < σ₁]
          → 精炼
Round N: 初始值 = Round N-1 最佳 + N(0, σₙ)
          → 收敛
```

#### Multi-Start（多起跑）

每次 Start 使用独立的随机种子，并行/顺序优化：

```
Start 0: seed=42 → 探索不同初始区域
Start 1: seed=123
Start 2: seed=456
最终取所有 Start 中最佳结果
```

每个 Start 独立优化，避免针对特定种子的过拟合。

#### Escape Exploration（逃逸探索）

检测到停滞时（连续 N 步无提升），主动加大扰动步长跳出局部最优。

#### Isolated Board Guard（孤立板保护）

当检测到的标定板数量发生突变（增多或减少）时，回滚到上一组参数，因为板数量变化通常意味着参数偏离合理范围。

### 4.4 评分函数

```
Score = Σ||p_i_proj - p_i_detected||² / N
```

- `p_i_proj`：基于当前参数投影的标定板角点理论位置
- `p_i_detected`：OpenCV 从 FBO 图像检测到的实际位置
- `N`：角点总数

分数越低越好。`0.0` = 完美对齐。

### 4.5 多阶段优化（Explore-Then-Refine）

```
Phase 1 — 探索:
  - σ 大（参数大步搜索）
  - 低分辨率 FBO 抓图（加速）
  - 粗评分

Phase 2 — 精炼:
  - σ 小（局部收敛）
  - 全分辨率 FBO 抓图
  - 细评分
```

---

## 五、图像采集管道

### 5.1 FBO 抓图机制

IPG-MOVIE 使用 OpenGL Frame Buffer Object 进行离屏渲染：

```
CameraCalibrator
  │
  ├─ DDE: View::SetSize(w, h)   ← 设置渲染尺寸
  ├─ DDE: Snapshot <path>        ← 触发离屏渲染
  │     ↓
  │   IPG-MOVIE 渲染引擎:
  │     glBindFramebuffer(GL_FRAMEBUFFER, fbo)
  │     glClear() / glDraw()
  │     glReadPixels() → .png
  │     ↓
  └─ cv2.imread(<path>)          ← 读回 Python
       ↓
     OpenCV 检测标定板
```

### 5.2 双路径降级

```
失败传导:
  FBO 创建失败 → 非致命，继续
  FBO 抓图超时 → 非致命，跳过该迭代
  完全不渲染  → FBO 健康检查报错 → kill_all → 重试
```

### 5.3 FBO 健康检查

```
check_movie_fbo():
  1. 创建 16x16 FBO
  2. 渲染测试图案
  3. 删除 FBO
  成功 = FBO 管道通畅
  失败 = GL 上下文损坏 → 杀进程重试
```

---

## 六、标定板检测

### 6.1 支持的类型

```
board_auto_detector.py 提供统一接口：

detect_board(image, board_type) → List[BoardResult]

board_type = "checkerboard" | "aruco" | "apriltag"
           | "charuco" | "circle_grid" | "arucogrid" | "custom"
```

### 6.2 Checkerboard 检测

```python
# OpenCV 标准流程
ret, corners = cv2.findChessboardCorners(
    gray, (cols, rows),
    flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
)
if ret:
    corners = cv2.cornerSubPix(gray, corners, ...)
```

### 6.3 ArUco/AprilTag 检测

```python
# OpenCV ArUco
dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_250)
corners, ids, _ = cv2.aruco.detectMarkers(image, dictionary)
```

### 6.4 多板重叠去重

Wizard 中的自动去重逻辑：

```
检测到的板列表:
  [Checkerboard(5x4, 区域A), ArUco(3个标记, 区域A), ...]

去重:
  按 OpenCV rect 区域计算 IOU
  IOU > 0.5 且为 Checkerboard + Tag 混合 → 保留 Checkerboard
```

### 6.5 自定义板（Custom Maker）

用户手动在标注图像上绘制矩形区域（红框），系统使用模板匹配：

```
1. 从标注图提取红色矩形 ROI
2. 对每个 ROI 提取模板图像
3. 运行时：模板匹配 + RANSAC 定位
```

---

## 七、标定板配置生成（Wizard）

### 7.1 Annotation Bootstrap

Wizard 的核心能力：从**标注图像**自动生成标定配置。

```
输入:
  - 参考图像 (real_image)
  - 标注图像 (annotated_image) — 带红色矩形框

处理流程:
  1. _extract_annotation_rectangles()
       → 在标注图上查找红色像素
       → 聚类为矩形框
       → 过滤掉过小/过大的矩形
  2. _extract_annotation_board_ids()
       → 对每个矩形区域做 OCR (RapidOCR)
       → 读取标注 ID 文字
  3. 根据矩形位置/大小确定板类型:
       - 多行多列矩形 → Checkerboard
       - 小矩形阵列 → ArUco
  4. 生成 _auto_template_crop() 模板
  5. 输出完整 bootstrap config

输出:
  - camera.<camera_name>.json
  - 模板图像（用于 template_match）
```

### 7.2 模板自动选择

当标定板为 Custom 类型时，系统自动选择最佳模板区域：

```
_select_auto_template_crop():
  1. 对标注图中的每个矩形 ROI
  2. 提取包含矩形 + 周围 20px 缓冲的图像块
  3. 计算纹理丰富度（梯度方差）
  4. 选纹理最丰富的区域作为模板
  5. 裁剪并保存为模板 PNG
```

### 7.3 棋盘格自动降级

当 OpenCV 在运行时检测不到完整的棋盘格时，自动降级为 template_match：

```
_auto_upgrade_partial_checkerboards():
  1. 尝试 cv2.findChessboardCorners
  2. 若失败，对棋盘格每个区域做模板匹配
  3. 若模板匹配也失败，标记该棋盘格为 "unmatched"
  4. 不删除该棋盘格定义（保留理论位置）
```

### 7.4 输出配置格式

生成的文件（`camera.<camera_name>.json`）结构：

```json
{
  "real_image": "Movie/ngxpro/<camera_name>.jpg",
  "annotated_image": "Movie/ngxpro/<camera_name>_origin.jpg",
  "boards": [
    {
      "type": "checkerboard",
      "cols": 5,
      "rows": 4,
      "size_m": 0.032,
      "rvec": [0.1, 0.2, -0.05],
      "tvec": [-1.5, 0.3, 8.0],
      "ids": [1]
    }
  ],
  "initial_values": { "Pos.X": 0.0, "Rot.Yaw": 0.0, ... },
  "bounds_multiplier": 1.5,
  "scoring_scope": "all",
  "bootstrap_templates": {
    "type": "auto_crop",
    "template_img": "..."
  }
}
```

---

## 八、车辆回写机制

### 8.1 写回流程

```
_write_best_values_to_vehicle_config(config_path, result):
  1. 读取 Vehicle XML 文件
  2. 找到目标传感器的 Sensor 节点
  3. 对比新参数 vs 历史参数池
  4. 若新参数明显更优 → 写入 Vehicle 文件
  5. 若历史已有更优 → 不写入（保护）
  6. 更新历史参数池 JSON
```

### 8.2 历史参数池

```
historical_params_pool.json:
{
  "version": 2,
  "pool": [
    {
      "score": 0.53,
      "params": { "Pos.X": 0.12, "Rot.Yaw": -1.3, ... },
      "result_path": ".../result.json",
      "timestamp": "2026-06-17T14:22:00"
    },
    ...
  ]
}
```

- 每次标定完成后更新
- 最多保留 100 条历史记录
- 写入 Vehicle 前与池中最佳对比：只有新结果显著更好（阈值 10%）才写入
- 防止迭代退化

### 8.3 初始值从车辆读取

```
capture_initial_values_to_config(cfg, vehicle_path):
  1. 解析 Vehicle XML
  2. 读取目标传感器的 Pos/Rot/FoV
  3. 写入 cfg["initial_values"]
  4. 若存在历史参数池，加载历史最佳作为补充初始值
```

---

## 九、DDE 通信

### 9.1 控制链路

```
Python ──DDE──→ CarMaker ──Tcl send──→ IPG-MOVIE

分层：
  Python 层:  dde_health_check.py / cmapi_testrun_control.py
              ↓ DDE "TclEval"
  CarMaker 层: Tcl 解释器执行脚本
              ↓ Tk "send"
  Movie 层:   Tk/Tcl 命令 (View::SetSize, abraxas enable, ...)
```

### 9.2 Script Control

参数通过 CarMaker 的 Script Control 功能写入：

```
DDE: TclEval
  → CarMaker 执行 script_control_apply.tcl
    → SetValue("Sensor.<cam>.Pos.X", new_value)
    → SetValue("Sensor.<cam>.Rot.Yaw", new_value)
    → ...
```

### 9.3 健康检查探针

8 项 DDE 探针逐步验证链路完整性：

| 探针 | 验证内容 | 预期响应 |
|------|---------|---------|
| 1. tcleval_ping | TclEval 基本响应 | "pong" |
| 2. interpreter_probe | 解释器注册状态 | interp name |
| 3. movie_command_probe | Movie 命令可用 | 非空 |
| 4. movie_ping | Movie DDE 响应 | "pong" |
| 5. movie_camera_probe | Camera 命名空间 | 可用 |
| 6. movie_view_probe | View 设置 | 可用 |
| 7. gpusensor_ping | GPU Sensor | 可用 |
| 8. movie_render/fbo_probe | 渲染/FBO 健康 | 正常 |

结果分类为 14 种诊断码（如 `OK`, `movie_not_running`, `render_frozen`, `fbo_failure` 等）。

---

## 十、进程管理

### 10.1 进程枚举

```python
# 使用 PowerShell WMI 枚举 CarMaker/Movie 进程
# 带 5 秒超时保护（修复 WMI 卡死问题）

PROCESS_ENUMERATION_COMMAND = """
$procs = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in @('CarMaker.win64.exe','HIL.exe','CM_Office.exe','Movie.exe') } |
    Select-Object ProcessId, Name, CommandLine
if (-not $procs) { write-output '[]'; return }
$procs | ConvertTo-Json -Compress
"""
```

当 WMI 卡死时（已知系统问题），5 秒超时返回空列表。

### 10.2 进程清理

两级清理策略：

| 场景 | 方法 | 保护 |
|------|------|------|
| 启动时 | `kill_existing_cm_processes()` → `taskkill /IM` | 3s 超时 |
| FBO 损坏恢复 | `kill_all_processes()` → `taskkill /IM` | 3s 超时 |
| 精确 PID 杀 | `kill_gui_movie_processes()` → `taskkill /PID` | 仅在 WMI 成功时 |

### 10.3 名称唯一性

| 进程名 | 用途 | 重要判断 |
|--------|------|---------|
| `CarMaker.win64.exe` | CarMaker 主进程 | 通过 DDE TclEval 通信 |
| `HIL.exe` | HIL 运行时 | 无 GUI，纯计算 |
| `CM_Office.exe` | CarMaker Office | 许可证管理 |
| `Movie.exe` | IPG-MOVIE | GUI 模式含 `-cmgui`，GPU Sensor 模式含 `-mode GPUSensor` |

---

## 十一、输出目录结构

### 11.1 当前结构（v1.1+）

```
SimOutput/
├── calibration/                  ← 标定运行根目录（新增于 Phase 43）
│   ├── right_rear/
│   │   ├── rounds_20260617_082722/
│   │   │   ├── round_00/
│   │   │   │   ├── start_00/     ← Multi-start 各起跑
│   │   │   │   │   ├── result.json
│   │   │   │   │   ├── run.log
│   │   │   │   │   └── best_overlay_*.png
│   │   │   │   ├── start_01/
│   │   │   │   └── start_02/
│   │   │   ├── round_01/
│   │   │   └── multistart_summary.json
│   │   ├── rounds_20260618_...
│   │   ├── camera_summary.json
│   │   └── historical_params_pool.json
│   ├── left_tv/
│   ├── rear_tv/
│   └── ...
├── camera_orchestration/         ← 编排器日志
├── dde_health_check/             ← DDE 健康检查记录
└── ipgmovie_health_monitor/      ← Movie 健康监控
```

### 11.2 关键文件说明

| 文件 | 格式 | 说明 |
|------|------|------|
| `result.json` | JSON | 单次标定结果（参数/分数/接受信息） |
| `run.log` | 文本 | 全量运行日志（stdout/stderr 重定向） |
| `task_summary.json` | JSON | 编排器汇总（多相机结果） |
| `camera_summary.json` | JSON | 相机历史摘要（所有运行记录） |
| `historical_params_pool.json` | JSON | 参数历史池（防回退） |
| `multistart_summary.json` | JSON | Multi-start 汇总（所有 start 结果） |
| `best_overlay_*.png` | PNG | 最佳结果叠加图 |
| `camera_summary_compact.json` | JSON | 精简版历史摘要（供 GUI 快速加载） |

---

## 十二、关键设计决策

### 12.1 为什么用子进程而非线程？

- 标定可能运行数小时
- 子进程崩溃不影响 GUI 响应
- 子进程可以被操作系统独立管理内存
- 代价：进程间通信需要序列化（前缀JSON协议）

### 12.2 为什么用 FBO 而非窗口截图？

| 方法 | 优点 | 缺点 |
|------|------|------|
| 窗口截图 (win32) | 简单，不需要 FBO | 窗口遮挡时失败，分辨率受限 |
| DDE 抓图 (FBO) | 离屏渲染，不受遮挡 | 依赖 GL 上下文，FBO 可能损坏 |
| Win32 GDI 截屏 | 备选方案 | 性能差，依赖窗口可见 |

权衡结果：**FBO 为首选，Win32 为降级备选**。

### 12.3 为什么不用 Optuna？

Optuna 曾是探索选项，但实际采用的策略更简单：

- **贪心 hill-climbing** + 随机扰动
- Simple, predictable, debuggable
- 标定参数空间相对平滑（物理约束保证）
- Optuna 的采样开销 > 收益

### 12.4 为什么用 WMI 枚举进程？

- 需要获取 `CommandLine` 区分 GUI Movie 和 GPU Sensor Movie
- `Get-Process` 在 PowerShell 5.1 中不提供 `CommandLine`
- 已知风险：WMI 卡死 → 已加 5s 超时保护
- taskkill `/IM` 作为不依赖 WMI 的备选

### 12.5 映射文件 vs 配置目录

之前系统同时支持两种路径发现方式：
1. `calibtool_camera_config.json` 映射文件（Wizard 写入）
2. 扫描约定目录下的 `.json` 文件

决策：**统一到映射文件**。单一真相源，避免路径冲突。

---

## 十三、性能分析

### 13.1 时间占比

单相机单 round 标定的时间分布：

| 阶段 | 占比 | 说明 |
|------|------|------|
| Prepare Runtime | ~40% | CarMaker 启动 + Movie 初始化 |
| 迭代优化（每步） | ~3-5s | 调参 → DDE → 抓图 → 检测 → 评分 |
| DDE 通信 | ~30% | 参数写入 + 状态查询 |
| FBO 抓图 | ~30% | 离屏渲染 + 文件 I/O |
| OpenCV 检测 | ~20% | 标定板检测 |
| 其他 | ~20% | 日志、JSON 序列化 |

### 13.2 瓶颈分析

```
主要瓶颈（按严重程度）:
  1. CarMaker/Movie 启动时间（20-60s）
  2. DDE 通信延迟（每条 ~50-200ms）
  3. FBO 文件 I/O（每帧 ~100-300ms）
  4. OpenCV 检测（每帧 ~50-200ms）
```

---

## 十四、测试策略

### 14.1 测试层级

| 层级 | 范围 | 工具 | 运行时间 |
|------|------|------|---------|
| Unit | 单个函数/类 | pytest | 秒级 |
| Integration | 模块间交互 | pytest + monkeypatch | 分钟级 |
| E2E | 完整标定流 | 编排器 CLI | 小时级 |
| Stress | 高并发 FBO/DDE | runtime_fbo_stress_20x | 自定义 |

### 14.2 关键测试文件

```
tests/
├── test_cmapi_testrun_control.py   ← DDE/Movie 控制
├── test_dde_health_check.py        ← 健康检查
├── test_fbo_score_check.py         ← FBO 抓图+评分
├── test_calibration_service.py     ← 标定服务
├── test_portable_runtime.py        ← 运行环境
├── test_static_vehicle_reader.py   ← 车辆文件解析
└── test_calib_start_flow.py        ← 启动流程
```

---

## 附录：术语表

| 术语 | 说明 |
|------|------|
| FBO | Frame Buffer Object，OpenGL 离屏渲染目标 |
| DDE | Dynamic Data Exchange，Windows 进程间通信 |
| ABRAXAS | IPG-MOVIE 的固定场景渲染模式 |
| TclEval | CarMaker 的 DDE 命令接口 |
| Tk send | Tcl/Tk 进程间命令传递 |
| Script Control | CarMaker 参数运行时修改机制 |
| Round | 一轮完整的 multi-start 优化 |
| Multi-Start | 多起跑线并行优化 |
| Bootstrap | 从标注图自动生成标定配置 |
| Optuna | 超参优化框架（备选方案） |
| CheckerViewport | IPG-MOVIE 的相机视口递归检查 |
| Pose | 位置 + 旋转（6自由度） |
| Annotated Image | 带标注框的参考图像 |
| Vehicle Writeback | 标定结果写回车辆配置文件 |
