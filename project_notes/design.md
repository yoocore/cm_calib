# IPGMovie 摄像头安装位置半自动标定方案设计文档

> **状态：⚠️ PARTIAL** — 架构原理仍有效，但阶段规划描述落后于实际进展。参见 `technical-principles.md` 获取最新信息。

## 1. 文档目的

本文档定义在当前 CarMaker 工程内实现“半自动摄像头标定匹配”的设计方案。

核心目标是：
- 在用户手动完成 UI 预置后（打开 IPGMovie、固定到目标 Camera 视角、打开 Settings），
- 由脚本自动执行参数微调、图像截取、标定板检测与迭代优化，
- 最终将仿真画面与真实图片的差异压缩到可接受范围内。

本文档不涉及立即扩展到全流程自动化（例如自动启动 TestRun、自动切相机视角）。

---

## 2. 背景与约束

### 2.1 当前业务背景

现有工程已具备：
- 摄像头仿真能力
- 标定场景搭建
- 真实图片基准数据

### 2.2 当前阻塞点

1. API 不能完整覆盖 IPGMovie 视角切换与截图保存。
2. 通过 InfoFile 修改摄像头安装位置需要重跑 TestRun，迭代成本高且画面更新链路不顺畅。
3. Vehicle-Sensor 的部分参数（如 scaling）非实时生效，不适合纳入实时闭环。

### 2.3 方案边界

本方案采用“人工预置 + 脚本闭环”模式：
- 人工负责一次性完成 IPGMovie 视角与 Settings 面板准备。
- 自动化仅负责 Settings 参数调节、截图、标定板检测与迭代优化。

### 2.4 标定板假设

本设计不再假设画面中只有单一标准标定板，而是支持“多种板型同时在场”。

结合当前工程中的实际板型，至少应支持：
1. 命名棋盘格板（如 B1-B4、S1-S5）
2. G1_left（地面标记区域）
3. G1_center（地面标记区域）
4. G1_right（地面标记区域）

关键约束：
- 多块命名板可能同时出现在同一帧画面中。
- 优化目标不是让某一块板变好，而是让整体标定效果变好。
- 若某一板显著恶化，即使其他板改善，也不应轻易接受该参数更新。

因此，系统必须采用“多板检测 + 多板联合评分 + 退化约束”的架构，而不是单板独立最优。

---

## 3. 总体方案概述

### 3.1 方案类型

半自动优化闭环（Human-in-the-loop + RPA + CV）：
- H：人工准备可操作状态。
- RPA：UI 自动修改安装位置参数。
- CV：自动检测标定板并评估仿真图与真实图的几何差异。
- OPT：基于差异驱动下一轮参数更新。

### 3.2 闭环目标函数

定义匹配分数 Score，值越小越好。

对于多板联合优化，推荐总评分函数：

TotalScore = Σ wi * BoardScore_i + λ * DegradePenalty

其中：
- BoardScore_i：第 i 类板的单板评分
- wi：对应板型权重
- DegradePenalty：对“某块板明显恶化”的惩罚项
- λ：退化惩罚权重

推荐单板评分函数：

BoardScore_i = a1 * RMSE_i + a2 * MaxError_i + a3 * MissRate_i

其中：
- 对于命名棋盘格板，RMSE_i 基于棋盘格角点误差
- 对于 G1 类地面标记板，RMSE_i 基于自定义锚点误差
- MissRate_i 表示该板关键点、锚点或目标区域的缺失率

停止条件：
- TotalScore 小于等于目标阈值 target_score，或
- 达到最大迭代次数，或
- 步长衰减到最小且连续无提升。

补充说明：
- 当任一关键板检测失败时，不进入正常评分，而直接施加高惩罚分。
- 当某一板的误差超过退化阈值时，应增加退化惩罚，必要时直接判定该 trial 不可接受。

---

## 4. 系统架构设计

### 4.1 模块划分

1. 配置模块
- 读取窗口识别规则、真实图路径、输出路径、参数边界与步长策略。

2. UI 连接模块
- 连接 IPGMovie 主窗口。
- 连接 Settings 窗口。
- 提供控件探测能力（Edit 列表导出）。

3. 参数执行模块
- 根据参数映射定位 Edit 控件。
- 写入数值并触发 Enter 提交。
- 等待画面稳定。

4. 截图模块
- 按 IPGMovie 窗口矩形区域截图。
- 命名并持久化用于回溯。

5. 评估模块
- 读取真实图与当前仿真图。
- 识别画面中的多种板型。
- 分别检测各板的角点、锚点或结构特征。
- 建立真实图与仿真图上每种板的点位对应关系。
- 计算每种板的单板误差、缺失率、退化状态与总分。
- 输出统一分数。

6. 优化模块
- 对每个参数执行双向试探（正向/反向）。
- 接受改进步并更新全局最优。
- 无改进时按策略缩步。

7. 结果归档模块
- 保存每轮 score、参数、截图路径。
- 输出 result.json 供复盘和复现。

### 4.2 数据流

1. 加载配置与真实图。
2. 对真实图执行一次多板检测并缓存各板参考特征。
3. 连接窗口并校验可操作状态。
4. 应用初值参数并截图评估。
5. 进入迭代：
- 逐参数双向试探
- 每次试探后截图评估
- 接受更优解或回滚
- 必要时缩步
6. 满足停止条件后输出最优结果。

---

## 5. 参数模型设计

### 5.1 参数结构

每个可优化参数包含：
- name：参数名
- initial：初始值
- step：当前步长
- min/max：边界
- min_step：最小步长
- decimals：写入精度
- widget_path：对应的 Script Control/Tk widget 路径说明，由主脚本内部统一维护

### 5.2 当前写参控制面

当前活动链路不再依赖 auto_id、title、field_index 这类 UI locator。

当前有效控制面是 Script Control 可访问的 Tk widget 树：
1. `.camera.presetFrame.*` 用于安装位姿参数
2. `.camera.cammoddlg.*` 用于 lens 参数
3. `.camera.btn.set` 用于提交写入

因此活动配置只需要表达参数值、边界、步长与精度，不再需要在 JSON 中保留 UI 定位字段。

### 5.3 参数分组建议

A 组（实时强相关，建议纳入闭环）：
- pos_x, pos_y, pos_z
- yaw, pitch, roll

B 组（非实时或弱相关，建议人工外环处理）：
- scaling
- 需重启/重跑才生效的 vehicle-sensor 参数

### 5.4 参数优化顺序建议

对于多板联合方案，建议采用分阶段优化顺序：
1. 优先优化平移参数 pos_x, pos_y, pos_z
2. 再优化姿态参数 yaw, pitch, roll

原因：
- 平移参数通常主导多块板在图像中的整体位置偏差。
- 姿态参数通常影响多板透视关系和局部几何分布。
- 先粗调位置、再细调姿态，更容易让多板联合评分进入有效收敛区间。

---

## 6. 优化算法设计

### 6.1 选择理由

采用坐标下降 + 双向试探，原因：
- 不依赖梯度，适合带检测噪声的几何特征误差评分。
- 与 Script Control 写参 + FBO 抓图的高成本单点评估匹配。
- 实现与调试成本低，便于快速落地。

### 6.2 单参数迭代逻辑

对参数 p：
1. 试探 p + step
2. 试探 p - step
3. 计算各板单板评分、总评分与退化惩罚
4. 仅当总评分改进超过 min_improve，且未触发不可接受退化时，接受更优方向
5. 否则保持原值并缩步

### 6.3 全局停止策略

停止条件建议：
- best_score <= target_score
- iteration >= max_iters
- 所有参数 step <= min_step 且当前轮无改进

补充约束：
- best_score 必须是联合总分，而不是任意单板最优分。
- 若连续多轮出现“单板改善但整体恶化”或“部分板显著退化”，应维持当前最优并继续缩步。

### 6.4 步长策略

- 初期使用相对较大步长快速收敛到邻域。
- 无改进时 step = max(min_step, step * step_decay)。
- 对角度与平移使用不同初始步长，避免尺度不一致。

### 6.5 检测失败惩罚策略

当仿真图未检测到足够板型特征时：
1. 直接返回高惩罚分，例如 1e6。
2. 将该步视为不可接受更新。
3. 保留截图与失败原因，便于定位参数越界或视野脱靶问题。

### 6.6 单板退化约束

为避免“优化 A 板时 B 板显著恶化”，建议增加单板退化约束：
1. 为每块板定义 degrade_threshold。
2. 若某块板的 RMSE、MaxError 或 MissRate 相比当前最优基线恶化超过阈值，则记为 degrade。
3. 若关键板触发 degrade，则该 trial 默认拒绝。
4. 若非关键板轻微 degrade，但整体改善明显，可按权重决定是否接受。

---

## 7. RPA 可靠性设计

### 7.1 运行环境约束

- Windows 缩放建议 100%。
- 固定分辨率与显示拓扑。
- 运行期间不移动/最小化窗口。
- 保持窗口前台可交互。

### 7.2 写入动作稳健策略

每个控件写入序列：
1. 焦点激活
2. 全选清空
3. 输入目标值
4. Enter 提交
5. 短等待 settle_sec

### 7.3 异常检测与恢复

应覆盖以下异常：
- 窗口未找到/失焦
- 控件索引越界
- 写入失败
- 截图失败
- 图像读取失败

恢复策略：
- 失败重试 N 次（建议 2 至 3）
- 超限后终止并输出现场信息（当前参数与最近截图）

### 7.4 可观测性

必须记录：
- 每次试探参数值
- 对应分数
- 对应截图路径
- 当前最优解

便于复盘以下问题：
- 收敛慢
- 抖动
- 局部最优
- UI 写入未生效

---

## 8. 图像匹配评估设计

### 8.1 检测对象

检测对象为多种标定板的组合场景，不以自然纹理特征为核心。

当前工程中的目标板包括：
- 命名棋盘格板（如 B1-B4、S1-S5）
- G1_left
- G1_center
- G1_right

### 8.2 板型检测策略

不同板型采用不同检测器：

1. G1 类地面标记板
- 采用自定义锚点检测
- 特征来源可包括圆心、特殊块中心、外框角点、L 形拐角等
- 输出一组有命名语义、顺序稳定的锚点

2. 命名棋盘格板
- 采用棋盘格角点检测
- 输出有序棋盘格角点

### 8.3 单板评分方法

对每块板，真实图与仿真图分别执行：
1. 灰度化或局部预处理
2. 按板型调用对应检测器
3. 建立该板的有序点位对应关系
4. 计算该板的误差指标

推荐指标：
- RMSE
- MeanError
- MaxError
- MissRate

单板综合分数优先采用：

BoardScore = RMSE + alpha * MissRate + beta * MaxError

其中 alpha、beta 为经验权重。

### 8.4 多板联合评分方法

当多块命名板同时出现在画面中时，必须基于全部可见板联合评分：

TotalScore = Σ wi * BoardScore_i + λ * DegradePenalty

推荐实践：
1. 为每种板配置独立权重 wi
2. 对 G1 类地面标记板设置较高权重，若其对外参更敏感
3. 对命名棋盘格板设置中等或分层权重
4. 对缺失检测、点数不足和单板显著恶化增加惩罚项

### 8.5 预处理建议

为了稳定检测，建议：
- 强制 resize 到同分辨率
 - 统一灰度处理
 - 按板型配置局部 ROI，减少背景干扰
 - 视需要进行轻量去噪或直方图均衡

### 8.6 ROI 设计建议

多板方案强烈建议使用分板 ROI，而不是整窗统一评估。

原因：
1. 各板在画面中的位置不同，统一 ROI 难以兼顾。
2. 可降低无关物体、阴影、反光的影响。
3. 可提升多板检测稳定性与收敛速度。

ROI 获取方式建议：
1. 对每种板单独配置初始 ROI
2. 根据真实图检测结果自动扩展一定 margin
3. 后续迭代固定 ROI 或小范围跟踪

### 8.7 阈值标定建议

建议通过历史数据离线标定 target_score、各板阈值与退化阈值：
1. 采集多组“主观匹配可接受”样本。
2. 统计各板 RMSE、MaxError、MissRate 分布。
3. 统计整体联合分数分布。
4. 取分位值（例如 P80）作为业务阈值。

### 8.8 检测器适配层设计

为避免后续更换板型时大改主流程，建议定义统一接口：
- detect(image, board_profile) -> detection_result
- score(real_detection, sim_detection, board_profile) -> board_score_detail

同时提供聚合层接口：
- aggregate(board_score_details) -> total_score_detail

detection_result 至少包含：
- success
- board_id
- board_type
- point_count
- ordered_points
- debug_image（可选）

board_score_detail 至少包含：
- board_id
- total_score
- rmse
- max_error
- miss_rate
- point_count

total_score_detail 至少包含：
- total_score
- degrade_penalty
- board_scores
- degraded_boards
- visible_board_count

---

## 9. 运行流程设计

### 9.1 运行前准备（人工）

1. 启动 CarMaker 并进入目标仿真状态。
2. 打开 IPGMovie。
3. 切换并固定到目标 Camera 视角。
4. 打开 Settings 面板并确保安装参数可编辑。

### 9.2 首次运行准备

1. 打开 Camera Settings，并确认安装参数页可编辑。
2. 至少打开一次 lens 页面，让 `.camera.cammoddlg` widget 树完成初始化。
3. 验证 Script Control DDE 可连通，运行时探针能返回当前 view 尺寸。

### 9.3 标定板初始化

1. 读取真实图。
2. 检测全部目标板并提取各板参考特征。
3. 若关键板检测失败，则流程直接终止。
4. 固化板型列表、各板 ROI 和阈值，作为本次优化任务上下文。

### 9.4 自动迭代执行

1. 启动优化。
2. 实时观察 score 下降趋势。
3. 结束后读取 result.json 中最优参数。
4. 人工确认最终视觉一致性。

### 9.5 结果固化

将最优参数回写到项目配置（或记录到变更清单），用于后续可复现仿真。

---

## 10. 配置与交付物设计

### 10.1 配置项

关键配置项：
- real_image
- script_control_dde_service
- script_control_dde_topic
- script_control_script_path
- script_control_result_path
- boards
- output_dir
- settle_sec
- target_score
- max_iters
- min_improve
- step_decay
- parameters

### 10.2 输出物

- 迭代截图序列
- result.json（含 history）
- 终值参数表

### 10.3 版本化建议

建议将以下内容纳入版本管理：
- 配置文件模板
- Script Control widget 约定与前置条件说明
- 阈值说明
- 最优参数快照

---

## 11. 验收标准

### 11.1 功能验收

1. 能通过 Script Control DDE 写入并读回参数。
2. 每轮能成功截图与评分。
3. 能产出 result.json 与完整历史。
4. 能在限制轮次内收敛或给出可解释停止原因。

### 11.2 指标验收

- 最终 total_score <= target_score（主标准）
- 所有关键板 RMSE <= 各自业务阈值
- 所有关键板 MissRate <= 各自业务阈值
- 不存在超过退化阈值的板
- 或与人工基准对比达到主观一致（辅标准）

### 11.3 稳定性验收

- 同一环境重复运行 3 次，结果差异在可接受区间。
- 完成一次 lens 页面初始化后，窗口可最小化且参数写入与 FBO 抓图可复用。

---

## 12. 风险与对策

1. Tk widget 树未初始化或 widget 名称变化导致写参失败
- 对策：首次运行前手动打开 lens 页面；写入前检查 `.camera` 与 `.camera.cammoddlg` 是否存在；将 widget 约定集中维护在 Script Control 主链中。

2. 评分函数与视觉感知不一致
- 对策：以多板几何误差替代通用纹理特征；增加分板 ROI；重标定阈值与板权重。

3. 局部最优
- 对策：多初值重启；人工给出更接近初值；分阶段优化（先平移后角度）。

4. 非实时参数扰动闭环
- 对策：将非实时参数移出本闭环，仅做外环人工调整。

5. 标定板检测不稳定
- 对策：固定光照与曝光；收缩分板 ROI；对棋盘格使用亚像素角点；对 GroundMaker 使用稳定锚点设计。

6. 单板改善但整体恶化
- 对策：引入多板联合评分和退化约束；关键板设置硬阈值，不允许以牺牲关键板换取总分虚假改善。

---

## 13. 后续演进路线

### 阶段 1（已完成）

半自动闭环稳定落地：
- 人工预置
- 脚本自动优化
- Script Control DDE 参数写入
- IPG-MOVIE DDE/FBO 离屏抓图

### 阶段 2（已完成）

增强稳健性与效率：
- 多板 ROI 配置化
- GroundMaker + 棋盘格双检测器支持
- 多帧多板加权评分
- 参数约束与联动策略
- 多相机编排器（calibration_orchestrator.py）
- CarMaker 运行态控制（cmapi_testrun_control.py）

### 阶段 3（已完成）

GUI 控制台与自动化：
- PySide6 GUI 控制台（三栏布局 + 底部进度）
- 状态机管理（IDLE → READY → PREPARING → RUNNING → FINISHED/FAILED）
- 策略切换（Multi-Start / Explore+Refine）
- 实时预览与结果展示（Current Iter / Best Score / Best Overlay）
- Auto-Prepare 智能流程（sensor 不匹配时自动切换）
- Portable EXE 打包交付

### 阶段 4（规划中）

逐步接近全自动：
- 利用可用 Tcl 命令进行完整视角控制
- 减少/消除人工准备步骤
- 自动任务编排与报告输出
- 多相机并行标定（当前为顺序执行）

---

## 14. 与当前实现对应关系

当前已落地脚本与文档：
- 主脚本见 [Data/Script/CameraCalibration/camera_calibration.py](Data/Script/CameraCalibration/camera_calibration.py)
- 多相机编排器见 [Data/Script/CameraCalibration/calibration_orchestrator.py](Data/Script/CameraCalibration/calibration_orchestrator.py)
- CarMaker 运行态控制见 [Data/Script/CameraCalibration/cmapi_testrun_control.py](Data/Script/CameraCalibration/cmapi_testrun_control.py)
- GUI 控制台见 [Data/Script/CameraCalibration/gui_app/](Data/Script/CameraCalibration/gui_app/)
- 当前 rear_tv 配置见 [Data/Script/CameraCalibration/configs/camera.rear_tv.json](Data/Script/CameraCalibration/configs/camera.rear_tv.json)
- 单一活动 Script Control 命令脚本见 [Data/Script/CameraCalibration/script_control_apply.tcl](Data/Script/CameraCalibration/script_control_apply.tcl)
- 使用说明见 [Data/Script/CameraCalibration/README.md](Data/Script/CameraCalibration/README.md)

说明：
- 当前仓库不再保留 overnight/best/final/proposed 命名的版本化配置变体；运行配置统一存放在 [Data/Script/CameraCalibration/configs](Data/Script/CameraCalibration/configs) 下，命名采用 camera.<camera>.json，例如 rear_tv 使用 [Data/Script/CameraCalibration/configs/camera.rear_tv.json](Data/Script/CameraCalibration/configs/camera.rear_tv.json)。
- 当前主链已收敛为纯 DDE/FBO：参数写入通过 Script Control DDE 发送到 IPG-MOVIE，抓图通过 FBO + gl readpixels 完成，不再依赖 IPGMovie 窗口连接或前台激活。
- 当前参数链仍依赖 `.camera` 与 `.camera.cammoddlg` widget 树；首次运行前需要手动打开一次 lens 页面，让对应控件完成初始化。
- 在 lens 页面完成一次初始化后，Script Control、Camera Settings 和 IPGMovie 窗口可保持最小化，短链路 smoke 已验证参数读写和 FBO 抓图可用。
- 多相机编排器支持顺序执行多个 camera 标定，自动处理 sensor 切换和运行态准备。
- GUI 控制台提供可视化操作界面，支持状态管理、实时预览、结果展示和 auto-prepare 智能流程。
- Auto-Prepare 流程：当 active sensor 不匹配时，自动触发 CM Prepare 切换 sensor，完成后自动开始标定。

本设计文档对应文件：
- [Data/Script/CameraCalibration/project_notes/design.md](Data/Script/CameraCalibration/project_notes/design.md)

实现规格拆解文档：
- [Data/Script/CameraCalibration/project_notes/spec.md](Data/Script/CameraCalibration/project_notes/spec.md)

GUI 设计蓝图：
- [Data/Script/CameraCalibration/project_notes/gui-control-blueprint.md](Data/Script/CameraCalibration/project_notes/gui-control-blueprint.md)

开发过程纪要：
- [Data/Script/CameraCalibration/project_notes/development_process.md](Data/Script/CameraCalibration/project_notes/development_process.md)
