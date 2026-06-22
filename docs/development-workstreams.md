# EXE GUI 与后端改造双线任务清单

> **⚠️ OUTDATED (2026-06-23)** — This document was written before the codebase split (12,608-line single file → 17+ modular files) and optimizer upgrade (P0-P8). Implementation details may not match the current codebase. See `codebase_split_plan.md` and `optimizer_upgrade_plan.md` for accurate current structure.

(The codebase has been split into 17+ modular files under src/calibration/ and linked directories. See `codebase_split_plan.md` for the current structure.)

## 1. 目标

当前开发分成两条主线并行推进：

1. EXE GUI 任务线
2. camera_calibration.py 与配套 orchestration 后端改造线

两条线的边界如下：

1. GUI 负责界面、状态机、日志呈现、任务发起与停止
2. 后端负责单 camera 标定、multi-camera 顺序编排、运行态切换、结构化结果输出
3. GUI 不直接内嵌重写标定算法
4. multi-camera 行为优先放在独立 orchestration 层，而不是把所有职责直接塞进 camera_calibration.py

## 2. 开发优先级

当前优先级从高到低如下：

1. 先打通后端 orchestration 最小闭环，使多个 camera 可以顺序执行
2. 给 GUI 补稳定的 machine-readable 输入输出协议
3. 搭建 EXE GUI 骨架并接上后端命令
4. 再细化运行态探测、结果面板与停止语义

## 3. EXE GUI 任务清单

### 3.1 M1 基础骨架

1. 新增 gui_app 目录结构
2. 创建 app.py 入口与 main_window.py 主窗口
3. 搭建三栏布局：Runtime、Calibration、Output
4. 定义 GUI 状态枚举与主窗口状态切换逻辑

### 3.2 M2 Runtime 面板

1. projectdir 选择与展示
2. TestRun 选择与展示
3. Vehicle 与 camera sensor 静态读取展示
4. active sensor 高亮刷新逻辑
5. CM Prepare 按钮与版本选择框

### 3.3 M3 Calibration 面板

1. camera 多选列表与顺序展示
2. 前置文件检查按钮与 per-camera 状态展示
3. rounds / explore / refine 参数输入区
4. 预估耗时显示
5. Calib Start / Calib Stop / Status 控件

### 3.4 M4 Output 面板

1. 输出目录展示与打开按钮
2. 实时日志窗口
3. 按 camera 分块的结果区域
4. 每个 camera 的 best score、score 视图、overlap 视图、current iter score 展示

### 3.5 M5 GUI 与后端接线

1. 用 QProcess 执行 runtime/prepare 命令
2. 用 QProcess 执行 multi-camera orchestration 命令
3. 解析结构化 JSON 事件并刷新界面
4. 停止任务时终止后端子进程并更新状态

## 4. 后端改造任务清单

### 4.1 B1 单 camera 结构化输出

1. camera_calibration.py 增加 machine-readable summary 输出
2. result.json summary 补充 current iter score 等 GUI 所需字段
3. 统一 stdout 中的结构化摘要前缀，便于 GUI 与 orchestration 解析

### 4.2 B2 multi-camera orchestration

1. 新增独立 orchestration 脚本
2. 接收本次任务的 camera 列表与顺序
3. 为每个 camera 切换 Vehicle active sensor
4. 为每个 camera 执行 start/stop testrun、Movie scene ready、健康检查
5. 顺序调用 camera_calibration.py 完成单 camera 标定
6. 汇总每个 camera 的独立结果，生成 task-level summary

### 4.3 B3 Runtime 控制接口收口

1. 评估 cmapi_testrun_control.py 增加 status/prepare machine-readable 模式
2. 收口 GUI 所需的 prepare 成功/失败返回结构
3. 明确 health check 结果的结构化字段

### 4.4 B4 停止与失败语义

1. 明确 multi-camera 任务停止时如何终止当前 camera
2. 明确已完成 camera 的部分结果如何保留
3. 明确失败 camera 如何记录 failure reason
4. 明确再次 Start 从头开始的任务重置语义

## 5. 当前已开始实现

当前已落地或正在落地的内容：

1. camera_calibration.py 已开始补充 machine-readable summary 输出
2. result summary 已补 current iter score 字段
3. 正在新增 multi-camera orchestration 脚本
4. 下一步开始创建 gui_app 第一版骨架
