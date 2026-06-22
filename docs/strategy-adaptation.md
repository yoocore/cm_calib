> **⚠️ OUTDATED (2026-06-23)** — This document was written before the codebase split (12,608-line single file → 17+ modular files) and optimizer upgrade (P0-P8). Implementation details may not match the current codebase. See `codebase_split_plan.md` and `optimizer_upgrade_plan.md` for accurate current structure.

- 当前项目运行编排约束：只允许单个 CarMaker 实例；若已有且仅 1 个则复用，否则清空后重启。TestRun 直接改原文件并保存，不走 SaveTestRun 副本链路；执行模型仅面向单个 TestRun，不纳入 TestManager。
- CameraCalibration 已新增可选 strategy_adaptation：可按已接受 move、joint candidate、当前最差 bottleneck boards 的改善历史动态重排参数顺序，并按 stagnation_count 在 baseline/expanded/aggressive exploration_profiles 之间切换 trial multipliers；不改变 acceptance 语义和参数硬边界。
- 外层 rounds 容易被首轮坏 basin 污染；已为 camera_calibration 加入可选 round_seeding，支持用 config/history best 作为 anchor，并在当前轮结果未优于 anchor 时拒绝接管下一轮 seed。
- 已在 _aggregate_scores 增加默认启用的 isolated_board_guard：当单块板相对 baseline 和同帧其他板出现孤立爆炸时，只把它记入 acceptance/history，不再让它主导优化 objective。用 004117 的真实数据验证后，S2 被单独隔离，objective_total_score 从 849.24 降到 62.78，而 C2/C4 未被一并隔离。
- 已新增可选 escape_exploration：multi-start 起点不再只做 step 级 jitter，可按上轮最差 boards、strategy current_param_order 和 round stagnation_rounds 生成 coarse_positive/coarse_negative/focused_escape 三类粗搜起点，用于跳出 history anchor 附近的局部盆地。
- objective_board_focus 目前作为受限自动策略使用，不常驻手工配置。right_rear 通过 auto_objective_board_focus 在连续停滞且出现至少两块同族高分板时，自动生成 objective_board_focus；触发对象不限于 C，可为任一板族，但仍要求同族聚集，避免被单块异常板带偏。