# 设计：Calib Start — 始终先运行 CM Prepare，再开始标定

**日期**: 2026-05-12  
**状态**: 草稿  
**作者**: opencode / brainstorming skill  

## 1. 概述

当用户点击 **Calib Start** 时，无论运行时当前是否 READY，GUI 都先执行一次完整的 CM Prepare（包括 TestRun 启停、IPG-MOVIE 窗口准备等已验证基线流程），等待 Prepare 成功后再启动标定。若 Prepare 失败，则中止并显示错误，不启动标定进程。这能以最安全的方式保证标定在正确的运行时环境中进行，且不依赖 UI 维护运行时的状态缓存。

## 2. 用户故事

> 作为标定工程师，我希望按下 "Calib Start" 后系统自动帮我准备好运行时环境，再开始标定。这样我不用担心运行时是否已就绪——无论状态如何，系统都会确保环境正确。如果准备过程失败，我希望看到清晰的错误信息。

## 3. 决策记录

- **选定方案**: 方案 3（Conservative — 始终先运行 CM Prepare）。  
- **优点**: 风险最小，不依赖 UI 运行时状态缓存；复用已验证的 Prepare 基线流程（参见 `verified_prepare_runtime_baseline_2026-05-12.md`）；行为可预测，减少标定环境不一致导致的故障排查时间。  
- **未选方案**:  
  - 方案 1（跳过 Prepare） — 需要预检逻辑保护，运行时中途漂移风险不可忽略。  
  - 方案 2（乐观启动 + 后台 Prepare） — 并发控制过于复杂，可能导致标定结果不一致。  
- **关键设计约束**: 现有 CLI 脚本（`calibration_orchestrator.py`、`cmapi_testrun_control.py`）是"真相层"，GUI 仅负责编排与展示。Prepare 流程复用 `RuntimeService.prepare()` 现有实现。

## 4. 架构与组件职责

```
┌─────────────────────┐
│ calibration_panel   │  UI：处理 Calib Start/Stop 点击、
│  (widgets/)         │  编排 预检 → Prepare → 标定 三级流水线
└────────┬────────────┘
         │ 调用
         ▼
┌─────────────────────┐
│ RuntimeService      │  prepare()：通过 ProcessService 运行
│  (services/)        │  cmapi_testrun_control.py，异步完成
└────────┬────────────┘
         │ 调用
         ▼
┌─────────────────────┐
│ CalibrationService  │  start_calibration()：无 skip 参数，
│  (services/)        │  始终执行完整标定流程
└────────┬────────────┘
         │ 调用
         ▼
┌─────────────────────┐
│ ProcessService      │  QProcess 封装：运行编排脚本、
│  (services/)        │  发射 line_received / orchestration_event / summary 信号
└────────┬────────────┘
         │ (启动两个子脚本：先 prepare 脚本，后 calibration 脚本)
         ▼
┌──────────────────────────────┐
│ cmapi_testrun_control.py     │  Prepare 真相层
│ calibration_orchestrator.py  │  标定真相层
└──────────────────────────────┘
```

### 4.1 `calibration_panel.py`（UI）

- **Calib Start 点击处理器**: 三级流水线（链式异步）：  
  1. 禁用 Start 按钮，启用 Stop 按钮，显示 "准备中..."  
  2. 调用 `PrecheckService.run_for_cameras(selected_cameras)`  
  3. 若预检失败 → modal 显示详情，恢复按钮，中止  
  4. 调用 `RuntimeService.prepare()`，监听其完成信号  
     - Prepare 进行中：状态栏显示 "CM Prepare 进行中..."  
     - Prepare 失败：显示错误，恢复按钮，中止  
  5. Prepare 成功后 → 调用 `CalibrationService.start_calibration(cameras)`（无 skip 参数）  
  6. 监听 ProcessService 的标定进度与摘要事件 → 更新输出面板  
  7. 标定结束（成功/失败）→ 显示摘要，恢复按钮  

- **Calib Stop 点击处理器**:  
  - 若处于 Prepare 阶段 → 终止 Prepare 进程  
  - 若处于标定阶段 → 终止标定进程  
  - 恢复按钮状态  

- **状态监听**: 监听 `ProcessService.orchestration_event` / `orchestration_summary` / `runtime_summary`，以及 `RuntimeService` 的 prepare 结果信号。

### 4.2 `RuntimeService`（`services/`）

- 复用现有 `prepare()` 方法，无改动。  
- 通过 `ProcessService` 运行 `cmapi_testrun_control.py`（已验证基线流程）。  
- 发射信号：`prepare_started`、`prepare_progress`、`prepare_finished(success: bool, message: str)`。  
- `calibration_panel` 订阅这些信号以驱动流水线。

### 4.3 `CalibrationService`（`services/`）

- **无改动**。启动标定的命令中不包含 `--skip-prepare` 标记，orchestrator 按其默认行为运行（即先 Prepare 后标定，或仅标定）。  
- 注意：由于 GUI 已经运行了 CM Prepare，如果 orchestrator 内部也运行 Prepare，可能会产生重复。  
  - **需要确认**: calibration_orchestrator.py 是否在启动时内部运行 Prepare？如果会，是否可让 GUI 跳过 orchestrator 内部的 Prepare？  
  - 临时方案：若 orchestrator 内部也运行 Prepare，重复执行可能导致冲突或延长总时间。需检查 orchestrator 的主流程。  
  - **设计决定**: 由本次设计调研后确定是否需要向 orchestrator 添加 `--skip-prepare` 标记（与方案 1 相同）。若有重复，添加标记；若无重复，跳过。

### 4.4 `PrecheckService`（`services/`）

- 现有服务，无需结构改动。  
- 校验项：camera 配置文件存在、movie 文件可访问、real_image 路径可读。  
- 返回结构化结果（`PrecheckResult`，包含 `ok: bool, details: list[str]`）。

### 4.5 `ProcessService`（`services/`）

- **不做改动**。  
- 复现有 stdout/stderr 解析与信号发射。

### 4.6 `calibration_orchestrator.py`（CLI，项目根目录）

- **需要调研**: orchestrator 内部是否已包含 Prepare 步骤。若包含，考虑添加 `--skip-prepare` 标记以避免 GUI 和 orchestrator 重复执行 Prepare。  
- 若不含 Prepare 步骤，则无需改动。

## 5. 数据流

```
用户点击 "Calib Start"
  │
  ├──1. 禁用 Start 按钮，启用 Stop 按钮，显示 "准备中..."
  │
  ├──2. PrecheckService.run_for_cameras(cameras) → {ok, details}
  │     └── 若 !ok → 显示 modal，恢复按钮，中止
  │
  ├──3. RuntimeService.prepare()            ← 异步（QProcess）
  │     ├── 状态："CM Prepare 进行中..."
  │     ├── 监听: runtime_summary / prepare_finished 信号
  │     ├── Prepare 成功 → 继续步骤 4
  │     └── Prepare 失败 → 显示错误，恢复按钮，中止
  │
  ├──4. CalibrationService.start_calibration(cameras)
  │     └── 命令不含 --skip-prepare（默认完整流程）
  │     └── ProcessService.start_process(cmd)
  │
  ├──5. ProcessService 流式输出 stdout
  │     └── 带前缀的 JSON 事件 → UI 更新（进度、状态、摘要）
  │
  ├──6. 标定进程结束
  │     ├── 成功：显示摘要，将 summary JSON 保存到输出目录
  │     └── 失败：显示错误，恢复按钮
  │
  └──7. 用户点击 "Calib Stop"（任意阶段）
        ├── Prepare 阶段 → 终止 cmapi_testrun_control.py
        ├── 标定阶段 → 终止 calibration_orchestrator.py
        └── 恢复按钮状态
```

## 6. CLI / 接口改动

### `CalibrationService.start_calibration()`

仅作为参考（无改动）：

```python
def start_calibration(self, cameras: list[str],
                      project_root: str,
                      output_dir: str) -> None:
    # 不添加 --skip-prepare 标记
    args = ["calibration_orchestrator.py",
            "--project-root", project_root,
            "--cameras", ",".join(cameras),
            "--output", output_dir]
    self.process_service.start_process([sys.executable] + args)
```

### `calibration_orchestrator.py`（待调研）

- 如果 orchestrator 内部已包含 Prepare 步骤，考虑添加 `--skip-prepare` 参数，但此非本设计必须项。  
- 在本设计的实施阶段需对此进行确认，并在文档中记录结论。

## 7. UI 行为与 UX 细节

| 状态 | Calib Start 按钮 | Calib Stop 按钮 | 状态显示 |
|------|-----------------|----------------|----------|
| Idle（初始） | 启用 | 禁用 | "就绪" |
| 预检进行中 | 禁用 | 禁用 | "正在运行预检..." |
| 预检失败 | 启用 | 禁用 | Modal：失败详情 + "请修复后重试" |
| CM Prepare 进行中 | 禁用 | 启用 | "CM Prepare 进行中..." |
| CM Prepare 失败 | 启用 | 禁用 | "CM Prepare 失败：<原因>" |
| 标定进行中 | 禁用 | 启用 | "标定进行中（Prepare 已完成）" |
| 标定完成 | 启用 | 禁用 | 显示摘要 + summary JSON 已保存 |
| 标定失败 | 启用 | 禁用 | 显示错误摘要，恢复按钮 |

- **预检失败 Modal**：标题 "预检失败"，正文列出每条失败项，一个按钮 "确定"。  
- **Prepare 进度**：在输出面板或状态栏显示 cmapi_testrun_control.py 的 stdout 输出。  
- **摘要显示**：在输出面板展示；同时将 `orchestration_summary.json` 保存到输出目录。  
- **Calib Stop 行为**：根据当前阶段终止对应进程（Prepare 阶段终止 Prepare；标定阶段终止标定）。

## 8. 错误处理与边界情况

| 场景 | 行为 |
|------|------|
| 预检失败（配置缺失、movie 文件不可读） | Modal 显示失败列表；中止；不执行 Prepare 或标定 |
| Prepare 失败（TestRun 无法启动、IPG-MOVIE 无法打开等） | 显示错误消息："CM Prepare 失败，请检查 CarMaker 环境"；不启动标定；恢复按钮 |
| Prepare 成功后 TestRun 在标定过程中退出 | Orchestrator 检测到并报告失败；UI 显示失败摘要 |
| 标定进程崩溃（QProcess 错误） | 捕获 stderr，显示错误消息，恢复按钮 |
| 用户在 Prepare 阶段点击 Calib Stop | 终止 cmapi_testrun_control.py；恢复按钮 |
| 用户在标定阶段点击 Calib Stop | 终止 calibration_orchestrator.py；恢复按钮 |
| orchestrator 内部也运行 Prepare（重复 Prepare） | 待调研确认；若存在重复，添加 --skip-prepare；若无重复，无改动 |

## 9. 需要改动的文件

| 文件 | 改动 |
|------|------|
| `gui_app/widgets/calibration_panel.py` | 重新编排 Calib Start：预检 → RuntimeService.prepare() → CalibrationService.start_calibration()（链式异步）；优化 Calib Stop 按阶段终止 |
| `gui_app/services/calibration_service.py` | 移除（或不添加）skip_prepare 参数；保持默认行为 |
| `gui_app/services/process_service.py` | 无改动 |
| `calibration_orchestrator.py` | 待调研：若内部含 Prepare 步骤，添加 `--skip-prepare` 标记（可选） |

## 10. 测试计划

### 单元测试
- `PrecheckService`：模拟文件存在性 -> 验证 `ok=True` / `ok=False` 及正确的 details。  
- `calibration_panel` 流水线：mock RuntimeService 和 CalibrationService，验证以下序列：
  - 预检通过 → prepare() 被调用  
  - prepare 成功 → start_calibration() 被调用  
  - prepare 失败 → start_calibration() 不被调用  
  - 预检失败 → prepare() 不被调用  

### 集成/手动验收
1. **正常路径**（CarMaker 环境）：  
   - 点击 Calib Start → 预检 → CM Prepare（TestRun 启动等）→ 标定开始 → 标定完成 → 显示摘要。  
2. **Prepare 失败**：  
   - 故意关闭 CarMaker 或 TestRun 不可用。  
   - 点击 Calib Start → 预检通过 → Prepare 失败 → 显示错误，不启动标定。  
3. **预检失败**：  
   - 删除某个配置文件。  
   - 点击 Calib Start → modal 显示失败 → 不执行 Prepare 或标定。  
4. **Calib Stop 中途中止**：  
   - Prepare 阶段点击 Stop → 终止 Prepare → 按钮恢复。  
   - 标定阶段点击 Stop → 终止标定 → 按钮恢复。  
5. **回归测试**：Runtime 面板的独立 Prepare 按钮仍正常工作。

## 11. 本规范不涉及的内容

- 偏好 UI（"始终跳过 CM Prepare" 复选框）— YAGNI，除非用户要求。  
- stdout 事件格式改动（前缀常量）。  
- 多 camera 选择 UI 改动（如果还未实现）。  
- Web/远程/多用户支持。

## 12. 开放问题（设计期间已解决）

- 问：Calib Start 是否在运行时已 READY 时跳过 Prepare？  
  答：**否** — 用户确认方案 3：始终先运行 CM Prepare。  
- 问：是否需要确认对话框？  
  答：**否** — 状态栏显示 "CM Prepare 进行中..." 已足够。  
- 问：CalibrationService 是否需要 skip_prepare 参数？  
  答：**否** —— 本标准设计中不使用该参数。但若 orchestrator 内部包含 Prepare 步骤，需添加 `--skip-prepare` 以避免重复。此问题将在实施阶段调研确认。

## 13. 待调研项（实施前需要确认）

1. **`calibration_orchestrator.py` 内部流**: 是否在执行标定之前内部调用了 prepare/TestRun 相关逻辑？  
   - 若是，需添加 `--skip-prepare` 标记以避免重复 Prepare。  
   - 若否，无需改动。  
2. **`RuntimeService.prepare()` 的信号接口**: 是否已发射 `prepare_finished(success, message)` 信号？若否，`calibration_panel` 如何检测 Prepare 完成？  
   - 可通过 `ProcessService` 的 `process_finished` / `process_failed` 信号配合 `runtime_summary` 事件来判断。
