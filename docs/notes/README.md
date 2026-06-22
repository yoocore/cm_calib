# CameraCalibration Project Notes

这个目录用于保存与当前 CameraCalibration 工程强相关的长期知识文档。

设计原则：
1. 与当前项目实现、运行约束、排障经验直接相关
2. 适合进项目目录并进入版本管理
3. 不依赖 Copilot memory 才能被团队成员理解和检索

## 文档状态分类

| 状态标签 | 含义 |
|---------|------|
| ✅ ACTIVE | 内容与当前实现一致，持续维护 |
| ⚠️ PARTIAL | 部分内容过时，核心原理仍有效 |
| ❌ OBSOLETE | 已被新方案取代，保留供参考 |
| 📜 HISTORICAL | 历史探索记录，无当前实用价值 |

## 当前文档

### ✅ ACTIVE — 活跃维护

1. `camera-rpa-notes.md`：CameraCalibration 主链长期经验笔记 — 传感器切换、进程清理、DDE 路径、优化策略等实战记录
2. `ipgmovie_control_workflow.md`：IPG-MOVIE 控制主文档 — DDE→CarMaker→Tk send 的完整控制链、FBO/渲染验证、WatchDog 等
3. `strategy-adaptation.md`：优化策略演化与当前约束总结（round_seeding、isolated_board_guard、escape_exploration 等）
4. `development_process.md`：项目演化全程记录（API 探索→RPA→DDE/FBO→GUI→Phase 37 工程化）
5. `technical-principles.md`：技术原理综述 — 架构、算法、关键设计决策（2026-06-18 新建）
6. `usage-guide.md`：使用流程指南 — 从环境准备到标定完成的完整操作步骤（2026-06-18 新建）
7. `requirements.txt`：当前维护的 Python 依赖清单

### ⚠️ PARTIAL — 部分过时

8. `design.md`：系统设计文档 — ⚠️ 架构原理仍有效，但"阶段 4 多相机并行"等规划描述已落后于实际进展
9. `spec.md`：实现规格 — ⚠️ 模块定义和接口规范仍有效，多相机编排部分需参考实际代码
10. `verified_prepare_runtime_baseline_2026-05-12.md`：Prepare 运行链基线 — ⚠️ 核心流程正确，但 Phase 38 后的精简（auto-prepare、按钮移除）未反映
11. `gui-control-blueprint.md`：GUI 控制层蓝图 — ⚠️ 四层架构/状态机设计仍有效，但 CM Prepare/Status 按钮已于 Phase 38 移除

### ❌ OBSOLETE — 已过时

12. `abraxas-dde-notes.md`：ABRAXAS 与 DDE / Tk send 故障面记录 — ❌ 问题已定位并修复，保留供历史追溯
13. `ipgmovie-health-normal-2026-05-09.md`：健康基线快照 — ❌ 临时快照，价值已过期
14. `ipgmovie-pre-reboot-snapshot-2026-05-10.md`：重启前坏态快照 — ❌ 临时快照，价值已过期
15. `ipgmovie_send_failure_dde_execute_summary_2026-05-10.md`：send 故障分析 — ❌ 问题已根因分析+修复，保留供追溯

### 📜 HISTORICAL — 历史归档

16. `historical/parameter-writing-evolution.md`：参数写入控制面的三代迭代历史（桌面点击 → 控件定位 → Script Control DDE）
17. `historical/capture-path-evolution.md`：抓图路径从窗口截图到 DDE/FBO 的探索历程

## 说明

1. 这里收纳的是当前工程需要版本管理的正式文档和长期笔记。
2. 新建文档时请标注日期和状态，便于后续维护者判断时效性。
3. 过时文档在标题添加 ❌ 标记，不删除（保留探索经验）。
