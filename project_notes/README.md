# CameraCalibration Project Notes

这个目录用于保存与当前 CameraCalibration 工程强相关的长期知识文档。

设计原则：

1. 与当前项目实现、运行约束、排障经验直接相关
2. 适合进项目目录并进入版本管理
3. 不依赖 Copilot memory 才能被团队成员理解和检索

当前文档：

1. `abraxas-dde-notes.md`：ABRAXAS 与 DDE / Tk send 故障面的补充记录
2. `verified_prepare_runtime_baseline_2026-05-12.md`：已验证 Prepare 运行链冻结基线
3. `../verify_runtime_chain_baseline.py`：冻结运行链的参考脚本模板，用于回归对照与后续实现对齐
4. `bootstrap-template-notes.md`：bootstrap 模板经验与健康检查说明
5. `camera-rpa-notes.md`：CameraCalibration 主链长期经验笔记
6. `gui-control-blueprint.md`：本地 GUI 控制层蓝图
7. `ipgmovie-health-normal-2026-05-09.md`：2026-05-09 的健康基线快照
8. `strategy-adaptation.md`：优化策略演化与当前约束总结
9. `development_process.md`：项目演化过程记录
10. `design.md`：设计文档
11. `spec.md`：实现规格
12. `ipgmovie_control_workflow.md`：IPG-MOVIE 控制主文档
13. `requirements.txt`：当前维护的 Python 依赖清单

说明：

1. 这里收纳的是当前工程需要版本管理的正式文档和长期笔记。
2. `development_process.md`、`design.md`、`spec.md`、`ipgmovie_control_workflow.md`、`verified_prepare_runtime_baseline_2026-05-12.md` 和 `requirements.txt` 现在统一在 `project_notes/` 下维护，作为当前工程的正式归档位置。
3. repo memory 中的长期经验仍可继续沉淀，但与当前项目强相关、需要版本管理的内容应优先落到这里。
