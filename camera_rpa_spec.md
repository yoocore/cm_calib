# IPGMovie 标定板半自动匹配实现规格说明

## 1. 文档定位

本文档是 [Data/Script/CameraCalibration/camera_rpa_design.md](Data/Script/CameraCalibration/camera_rpa_design.md) 的实现级拆解版本。

目标不是重新讨论方案，而是回答以下工程问题：
- 系统应拆成哪些模块
- 各模块的输入输出是什么
- 配置文件需要包含哪些字段
- 状态机如何流转
- 结果文件如何组织
- 异常如何传播和终止

本文档用于指导后续 Python 脚本重构与实现，不包含具体代码实现。

当前仓库约定：
- 版本化输入按摄像头分别命名为 [Data/Script/CameraCalibration/camera_rpa_config.<camera>.json](Data/Script/CameraCalibration/camera_rpa_config.rear_tv.json)，例如当前 rear_tv 使用 [Data/Script/CameraCalibration/camera_rpa_config.rear_tv.json](Data/Script/CameraCalibration/camera_rpa_config.rear_tv.json)
- Script Control 命令脚本只保留一个活动入口：[Data/Script/CameraCalibration/script_control_camera_tree_probe.tcl](Data/Script/CameraCalibration/script_control_camera_tree_probe.tcl)
- runtime wrapper [Data/Script/CameraCalibration/copilot_script_control_runtime.tcl](Data/Script/CameraCalibration/copilot_script_control_runtime.tcl) 由脚本运行时生成，不作为手工维护配置

---

## 2. 实现边界

### 2.1 本阶段必须实现

1. 手动预置 IPGMovie 到目标 Camera 视角。
2. 手动打开 Settings 对话框。
3. 脚本连接窗口并写入安装位置参数。
4. 脚本截图 IPGMovie 画面。
5. 脚本基于多板检测计算联合误差分数。
6. 脚本迭代更新参数直至收敛或终止。
7. 输出完整日志、截图和结果文件。

### 2.2 本阶段不实现

1. 自动启动 CarMaker 或 TestRun。
2. 自动切换 IPGMovie 到 Camera 视角。
3. 自动修改非实时参数，例如 scaling。
4. 多窗口、多实例并发优化。
5. 分布式运行。

---

## 3. 顶层模块清单

系统应至少拆为以下模块：

1. ConfigLoader
2. WindowConnector
3. ControlInspector
4. ParameterRepository
5. ParameterWriter
6. MovieCapture
7. BoardDetector
8. ScoreEvaluator
9. ScoreAggregator
10. Optimizer
11. RunRecorder
12. CalibrationOrchestrator

---

## 4. 模块规格

## 4.1 ConfigLoader

### 职责
- 读取 JSON 配置文件
- 校验字段完整性、类型、取值范围
- 生成内部配置对象

### 输入
- config_path: str

### 输出
- CalibrationConfig

### 失败条件
- 文件不存在
- JSON 非法
- 必填字段缺失
- 参数边界不合法

### 错误语义
- 抛出 ConfigError

---

## 4.2 WindowConnector

### 职责
- 连接 IPGMovie 主窗口
- 连接 Settings 窗口
- 校验窗口处于 visible + ready 状态

### 输入
- movie_window_title_re: str
- settings_window_title_re: str
- backend: str，默认 uia

### 输出
- movie_window_handle
- settings_window_handle

### 失败条件
- 未找到窗口
- 窗口存在但未 ready
- 多个窗口匹配导致歧义

### 错误语义
- 抛出 WindowConnectError

---

## 4.3 ControlInspector

### 职责
- 枚举 Settings 窗口内可编辑控件
- 输出控件索引、title、auto_id、class_name
- 辅助建立参数与控件映射

### 输入
- settings_window_handle

### 输出
- List[ControlDescriptor]

### ControlDescriptor 字段
- index: int
- title: str
- auto_id: str
- class_name: str
- control_type: str

### 使用场景
- 首次接入
- UI 版本变化后重新映射

---

## 4.4 ParameterRepository

### 职责
- 管理所有待优化参数的当前值、边界、步长与 locator
- 提供参数快照与回滚能力

### 输入
- parameters_config

### 输出
- List[ParameterSpec]

### ParameterSpec 字段
- name: str
- initial: float
- current: float
- step: float
- min_value: float
- max_value: float
- min_step: float
- decimals: int
- locator_type: enum(auto_id/title/field_index)
- locator_value: str | int
- group: enum(position/orientation/other)

### 方法要求
- snapshot() -> ParameterSnapshot
- restore(snapshot)
- set_value(name, value)
- get_value(name)
- clip(name, value)
- decay_step(name)

---

## 4.5 ParameterWriter

### 职责
- 根据 locator 找到 Edit 控件
- 将参数写入控件并触发提交
- 支持写入结果确认

### 输入
- settings_window_handle
- ParameterSpec
- value: float

### 输出
- WriteResult

### WriteResult 字段
- success: bool
- written_text: str
- elapsed_ms: float
- error_message: Optional[str]

### 标准写入序列
1. 焦点切换到目标控件
2. 全选清空
3. 输入数值文本
4. 发送 Enter
5. 等待 settle_sec

### 可选增强
- 读回控件文本并确认与目标值一致

### 失败条件
- 控件不存在
- 控件不可编辑
- 输入被 UI 拒绝

---

## 4.6 MovieCapture

### 职责
- 按 IPGMovie 窗口矩形进行截图
- 保存截图到输出目录
- 记录截图元信息

### 输入
- movie_window_handle
- tag: str
- output_dir: str

### 输出
- CaptureResult

### CaptureResult 字段
- success: bool
- image_path: str
- width: int
- height: int
- timestamp: str
- error_message: Optional[str]

### 命名规范
- 初始图: iter_0000.png
- 参数试探图: iter_0003_pos_x_p.png
- 失败图: fail_iter_0012_pos_y.png

---

## 4.7 BoardDetector

### 职责
- 根据板型配置检测单块标定板
- 输出该板的有序角点或锚点
- 支持不同板型的专用检测逻辑与调试图

### 输入
- image: np.ndarray
- board_profile
- preprocess_options

### 输出
- DetectionResult

### DetectionResult 字段
- success: bool
- board_id: str
- board_type: str
- point_count: int
- ordered_points: List[[x, y]]
- refined: bool
- roi_used: Optional[Rect]
- debug_image_path: Optional[str]
- error_message: Optional[str]

### 约束
- ordered_points 必须具备稳定顺序，保证真实图和仿真图可逐点对齐。
- 若检测失败，ordered_points 为空。

### 本阶段优先实现
- CheckerboardDetector，用于命名棋盘格板（如 B1-B4、S1-S5）
- GroundMakerDetector，用于地面标记区域（如 G1_left、G1_center、G1_right）

### 后续扩展
- CharucoDetector
- ArucoBoardDetector

---

## 4.8 ScoreEvaluator

### 职责
- 接收单块板的真实图检测结果与仿真图检测结果
- 计算单板误差指标与单板综合分数

### 输入
- real_detection: DetectionResult
- sim_detection: DetectionResult
- board_profile
- scoring_config

### 输出
- BoardScoreDetail

### BoardScoreDetail 字段
- success: bool
- board_id: str
- total_score: float
- rmse: float
- mean_error: float
- max_error: float
- miss_rate: float
- matched_point_count: int
- failed_reason: Optional[str]

### 评分公式
推荐默认公式：
- total_score = rmse + alpha * miss_rate + beta * max_error

### 失败评分策略
当 sim_detection.success 为 false 或 point_count 小于最小阈值时：
- success = false
- total_score = fail_penalty
- failed_reason = 检测失败原因

---

## 4.9 ScoreAggregator

### 职责
- 接收所有板的单板评分结果
- 计算联合总分、退化惩罚和接受条件

### 输入
- List[BoardScoreDetail]
- aggregate_config
- baseline_board_scores

### 输出
- TotalScoreDetail

### TotalScoreDetail 字段
- success: bool
- total_score: float
- degrade_penalty: float
- board_scores: List[BoardScoreDetail]
- degraded_boards: List[str]
- visible_board_count: int
- failed_reason: Optional[str]

### 推荐聚合公式
- total_score = Σ wi * board_score_i + lambda * degrade_penalty

### 接受规则
- 总分必须优于当前 best_score 至少 min_improve
- 关键板不得超过 degrade_threshold
- 若某块板检测失败且其为关键板，则直接拒绝当前 trial

---

## 4.10 Optimizer

### 职责
- 执行参数搜索策略
- 调用参数写入、截图、单板评分与总分聚合模块
- 维护全局最优解

### 输入
- ParameterRepository
- scoring_thresholds
- optimization_strategy

### 输出
- OptimizationResult

### OptimizationResult 字段
- success: bool
- stop_reason: str
- best_score: float
- best_values: Dict[str, float]
- best_metrics: ScoreDetail
- iteration_count: int
- best_image_path: str

### 本阶段搜索策略
- 坐标下降
- 单参数双向试探
- 无改进缩步
- 先 position 后 orientation
- 基于联合总分接受或拒绝 trial
- 对关键板退化设置硬约束

### 必须支持的 stop_reason
- target_reached
- max_iters_reached
- all_steps_minimum
- window_lost
- write_failed
- capture_failed
- detection_failed_on_reference
- fatal_exception

---

## 4.11 RunRecorder

### 职责
- 记录每轮操作上下文
- 输出结构化 json 与可读日志

### 输入
- IterationRecord
- OptimizationResult

### 输出
- result.json
- run.log
- 可选 debug 图

### IterationRecord 字段
- iter: int
- phase: str
- parameter_name: str
- direction: str
- trial_value: float
- total_score: float
- degrade_penalty: float
- board_scores: array
- accepted: bool
- image_path: str
- parameter_snapshot: Dict[str, float]
- timestamp: str

---

## 4.12 CalibrationOrchestrator

### 职责
- 串联全流程
- 管理状态迁移与错误边界
- 决定何时终止

### 输入
- CalibrationConfig

### 输出
- 最终 OptimizationResult

### 调度顺序
1. 加载配置
2. 连接窗口
3. 可选 inspect 模式
4. 加载真实图并检测全部参考板
5. 初始化参数仓库
6. 进入优化循环
7. 产出结果文件

---

## 5. 状态机规格

系统状态建议定义如下：

1. INIT
2. CONFIG_READY
3. WINDOWS_READY
4. REFERENCE_READY
5. OPTIMIZING
6. CONVERGED
7. FAILED
8. FINISHED

### 状态迁移

- INIT -> CONFIG_READY
  条件：配置加载成功

- CONFIG_READY -> WINDOWS_READY
  条件：窗口连接成功

- WINDOWS_READY -> REFERENCE_READY
  条件：真实图标定板检测成功

- REFERENCE_READY -> OPTIMIZING
  条件：初值参数写入成功且首帧评估成功

- OPTIMIZING -> CONVERGED
  条件：best_score <= target_score

- OPTIMIZING -> FAILED
  条件：致命错误或必要依赖丢失

- OPTIMIZING -> FINISHED
  条件：达到非成功停止条件但流程正常收尾

- CONVERGED -> FINISHED

- FAILED -> FINISHED

---

## 6. 配置文件规格

## 6.1 顶层字段

必须字段：
- movie_window_title_re: string
- settings_window_title_re: string
- real_image: string
- output_dir: string
- boards: array
- parameters: object

建议字段：
- settle_sec: number
- target_score: number
- max_iters: int
- min_improve: number
- step_decay: number
- fail_penalty: number
- aggregate_weights: object
- degrade_lambda: number
- optimization_order: [string]

## 6.2 boards 子项字段

每个 board 必须包含：
- board_id
- board_type
- roi
- critical

对于棋盘格类板，必须包含：
- board_size
- square_size

对于自定义板，必须包含：
- template_image 或 template_points
- detector_profile

建议增加：
- weight
- degrade_threshold
- min_detected_points
- description

## 6.3 parameters 子项字段

每个参数必须包含：
- initial
- step
- min
- max
- min_step
- decimals

每个参数必须至少提供一种 locator：
- field_index
- auto_id
- title

建议增加：
- group
- unit
- description

## 6.4 board_type 取值

允许取值：
- checkerboard
- custom_groundmaker

本阶段优先实现：
- checkerboard
- custom_groundmaker

---

## 7. 结果文件规格

## 7.1 result.json 顶层结构

- run_id: string
- start_time: string
- end_time: string
- duration_sec: number
- success: bool
- stop_reason: string
- best_score: number
- best_values: object
- best_metrics: object
- best_image: string
- config_snapshot: object
- history_count: int
- history: array

## 7.2 best_metrics 结构

- rmse: number
- mean_error: number
- max_error: number
- miss_rate: number
- point_count: int

## 7.3 history 结构

每个元素对应一次 trial，字段参考 IterationRecord。

---

## 8. 日志规格

建议同时输出：

1. 人类可读日志 run.log
- 面向调试与回看
- 一行一个关键事件

2. 结构化结果 result.json
- 面向后处理与可视化

### 推荐日志事件
- config_loaded
- windows_connected
- reference_detected
- parameter_written
- frame_captured
- board_detected
- score_computed
- trial_accepted
- trial_rejected
- step_decayed
- optimization_stopped
- result_saved

---

## 9. 失败语义与恢复策略

## 9.1 可恢复错误

- 单次写入失败
- 单次截图失败
- 单次仿真图检测失败

策略：
- 当前 trial 重试 2 到 3 次
- 仍失败则将该 trial 记为失败并返回惩罚分

## 9.2 不可恢复错误

- 真实图标定板检测失败
- 主窗口或 Settings 窗口消失
- 配置非法
- 输出目录不可写

策略：
- 立即终止流程
- 输出 stop_reason 和现场信息

---

## 10. 性能与运行要求

### 10.1 单轮预算

建议把单轮 trial 时间控制在以下范围：
- 参数写入: 小于 500 ms
- 画面稳定等待: 300 到 500 ms
- 截图: 小于 200 ms
- 检测与评分: 小于 300 ms

目标：
- 单轮总耗时尽量控制在 1 秒级别

### 10.2 环境要求

- Windows 缩放 100%
- 单显示器或固定显示器布局
- IPGMovie 主窗口与 Settings 窗口始终可见
- 禁止人工同时操作窗口

---

## 11. 测试规格

## 11.1 单元测试关注点

1. 配置校验
2. locator 解析
3. 参数裁剪
4. 单板检测结果评分
5. 多板总分聚合
6. 退化约束判定
7. stop_reason 判定

## 11.2 集成测试关注点

1. 窗口连接正确性
2. 写入后 UI 数值是否生效
3. 截图区域是否正确
4. 三种板检测稳定性
5. 多板聚合评分是否合理
6. 收敛趋势是否合理

## 11.3 验证用例建议

1. 正常收敛用例
2. 单板脱靶但其他板可见用例
3. 某一板改善但另一板显著恶化用例
4. Settings 控件映射错误用例
5. 窗口关闭中断用例
6. 检测失败惩罚用例

---

## 12. 与现有原型的映射关系

当前原型文件：
- [Data/Script/CameraCalibration/camera_rpa_match.py](Data/Script/CameraCalibration/camera_rpa_match.py)

建议重构方向：
1. 保留 WindowConnector / ParameterWriter / MovieCapture 的基本思路
2. 将 ORB 评分逻辑替换为 BoardDetector + ScoreEvaluator
3. 在其上增加 ScoreAggregator，统一处理多板总分与退化约束
4. 引入状态机与 stop_reason 统一语义
5. 将结果输出从“可用”提升到“可审计”

---

## 13. 后续实现顺序建议

建议按以下顺序实施：

1. 先完成配置 schema 与窗口连接层
2. 再完成 CheckerboardDetector 与 GroundMakerDetector
3. 再完成单板评分器与多板聚合器
4. 再将现有优化循环改造成模块化 Optimizer
5. 最后补齐 RunRecorder、状态机和异常恢复

这样可以在最短路径上形成一个“标定板可跑通版本”，再逐步补强稳健性。

---

## 14. 对下一步拆解的输入

若继续向更细实现拆解，建议下一步输出三份子文档：
1. 棋盘格检测与评分详细规格
2. RPA 控件映射与交互详细规格
3. 优化器算法与参数调优详细规格
