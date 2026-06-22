> **⚠️ OUTDATED (2026-06-23)** — This document was written before the codebase split (12,608-line single file → 17+ modular files) and optimizer upgrade (P0-P8). Implementation details may not match the current codebase. See `codebase_split_plan.md` and `optimizer_upgrade_plan.md` for accurate current structure.

- bootstrap 生成的 custom_maker 模板不应再从人工 ROI 内自动二次猜小块；默认应把整块 template_source_roi 作为 template_source_crop。否则容易像 right_rear C3 一样误截到场景纹理。
- 已新增 `Data/Script/CameraCalibration/bootstrap_template_health_check.py`，可离线检查 bootstrap custom 模板是否异常偏小，以及模板图尺寸是否与 `template_source_crop` 一致。
