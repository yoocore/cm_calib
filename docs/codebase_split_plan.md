# camera_calibration.py 代码拆分计划

## 现状

- 单文件 12,600+ 行
- 仅一个 `CameraCalibrator` 类（L5650 起，~7,000 行方法）+ 模块级函数（~5,650 行）
- 方法间通过 `self.*` 共享状态，依赖关系密集

## 策略：Mixin 继承链

CameraCalibrator 类的方法分散到多个 Mixin 类中，通过多重继承重新组装。

**优势**：
- `self.*` 调用零改动（Python MRO 自动解析）
- 每个文件 300-1,200 行，职责单一
- 渐进式拆分：每拆一个 Mixin，立即验证

**Mixin 依赖关系（MRO 顺序）**：

```
CameraCalibrator (__init__ 留存)
├── DetectorMixin          ← _detect_*, _prepare_eval_image
├── ScoringMixin           ← _score_board, _aggregate_scores
├── AnnotationMixin        ← annotate_existing_image, _build_sim_eval_image
├── ScriptControlMixin     ← _apply_value_map, capture_movie
├── EvaluateMixin          ← evaluate()
├── CoordinateDescentMixin ← _optimize_coordinate_descent_impl, trial/strategy
├── BayesianOptimizerMixin ← _optimize_bayesian_impl
├── OrchestrationMixin     ← multi-start, rounds, history, pool, vehicle, autotune
└── CLI (模块级函数)       ← main(), argparse
```

## 文件结构

```
CameraCalibration/
├── camera_calibration.py          # 新主文件 (~250行)：组装类 + imports
└── calibration/
    ├── __init__.py                # 空
    ├── types.py                   # 所有 @dataclass (BoardProfile, DetectionResult, ...)
    ├── utils.py                   # 模块级工具函数
    ├── config.py                  # DEFAULT_CONFIG + bootstrap_config_from_annotation
    ├── detector.py                # DetectorMixin
    ├── scoring.py                 # ScoringMixin
    ├── annotation.py              # AnnotationMixin
    ├── script_control.py          # ScriptControlMixin (DDE + capture + preflight)
    ├── evaluate.py                # EvaluateMixin
    ├── optimizer_cd.py            # CoordinateDescentMixin
    ├── optimizer_bayesian.py      # BayesianOptimizerMixin
    ├── orchestration.py           # OrchestrationMixin (multi-start + rounds + pool + history + autotune)
    ├── strategy.py                # strategy_adaptation (_update_strategy_state, _ordered_params_for_iteration, etc.)
    └── cli.py                     # CLI 入口 (main, argparse)
```

## 各文件详情

### Phase 1: types.py (~300 行)

**内容**：所有 `@dataclass` 定义

- `BoardProfile`
- `DetectionResult`
- `BoardScoreDetail`
- `TotalScoreDetail`
- `ParameterSpec`
- `TrialResult`
- `EvalImageTransform`
- `StrategyProfile`
- 枚举常量 (BoardType, ComparisonMode)

**独立**，无 import。

---

### Phase 2: utils.py (~400 行)

**内容**：模块级无状态函数

- `_format_scalar_value_map`, `_camera_name_from_output_dir`
- `_quantize_float`, `_safe_dict_update`, `_build_explicit_parameter_config`
- `_board_prototype_family`, `_is_aruco_family_board_type`, `_is_custom_marker_board_type`
- `_is_circle_grid_board_type`, `_is_apriltag_board_type`, `_is_aruco_grid_board_type`
- `_is_charuco_board_type`
- `_build_annotation_legend_lines`, `_format_value_lines`
- `_find_config_file`, `_resolve_anchor_image`

**依赖**：仅 types.py

---

### Phase 3: config.py (~500 行)

**内容**：
- `DEFAULT_CONFIG` dict（行 ~400-5650 的默认配置）
- `bootstrap_config_from_annotation()` 函数
- `_custom_marker_template_image_path()`
- `propose_boards_config()`

**依赖**：types.py, utils.py

---

### Phase 4: detector.py (~1,200 行) — DetectorMixin

**方法清单**（CameraCalibrator 类方法）：

| 方法 | 行 | 说明 |
|------|-----|------|
| `_extract_roi` | 8140 | ROI 裁剪 |
| `_detect_roi_padding_attempts` | 8164 | 生成 padding 层级 |
| `_get_eval_transform` | 8399 | 坐标变换 |
| `_map_eval_points_to_source` | 8464 | 反向映射 |
| `_prepare_eval_image` | 8366 | 图像预处理 |
| `_build_sim_eval_image` | 8912 | 残差图 |
| `_preprocess_variants` | 8937 | CLAHE/高斯变体 |
| `_resolve_aruco_dictionary` | 8944 | ArUco 字典 |
| `_flatten_aruco_marker_points` | 8957 | 展平角点 |
| `_detect_checkerboard` | 8985 | 棋盘检测 |
| `_detect_aruco` | 9044 | ArUco 检测 |
| `_detect_apriltag` | ~9200 | ArUco 检测 |
| `_detect_board` | 9575 | 分发检测 |
| `_detect_reference_boards` | ~9600 | 参考板检测 |
| `_checkerboard_outline` | 6526 | 外框计算 |
| `_reference_detection_from_board_geometry` | 6559 | 几何检测 |
| `_anchors_from_bbox` | ~6575 | BBox→锚点 |
| `_custom_board_content_geometry` | ~6620 | 自定义板 |
| `_load_template_image` | ~6680 | 模板加载 |
| `_detect_template_match_board` | 9439 | 模板匹配 |

**依赖**：types.py, utils.py
**self 访问**：`self.real_img`, `self.real_img_color`, `self.boards`, `self.comparison_mode`, `self.keep_aspect_resize`

---

### Phase 5: scoring.py (~800 行) — ScoringMixin

| 方法 | 说明 |
|------|------|
| `_effective_detection_min_points` | 检测点数要求 |
| `_effective_scoring_min_points` | 评分点数要求 |
| `_is_visible` | 可见性判断 |
| `_score_board` | 单板评分 |
| `_aggregate_scores` | 总分聚合 |
| `_compute_degrade_penalty` | 退化惩罚 |
| `_snapshot_values` | 当前值快照 |
| `_record_iteration` | 记录到 history |

**依赖**：types.py, detector.py
**self 访问**：`self.boards`, `self.compare_only_if_reference_visible`

---

### Phase 6: annotation.py (~600 行) — AnnotationMixin

| 方法 | 说明 |
|------|------|
| `annotate_existing_image` | 主标注函数 |
| `_ensure_best_score_image` | 得分图生成 |
| `_ensure_best_overlay_image` | 叠加图生成 |
| `_best_score_image_output_path` | 路径生成 |
| `_best_overlay_image_output_path` | 路径生成 |
| `_get_annotation_palette` | 调色板 |
| `_resolve_annotated_label_anchor` | 标签位置 |
| `_draw_annotated_label` | 标签绘制 |
| `_build_score_image_for_snapshot` | 快照得分图 |

**依赖**：types.py, detector.py, scoring.py
**self 访问**：`self.real_detections`, `self.real_img`, `self.boards`, `self.cfg`

---

### Phase 7: script_control.py (~900 行) — ScriptControlMixin

| 方法 | 说明 |
|------|------|
| `capture_movie` | DDE 截图 |
| `_capture_movie_via_dde` | DDE 实现 |
| `_force_update_view` | 强制刷新 |
| `_diagnose_carmaker_after_failure` | 错误诊断 |
| `_is_black_frame` | 黑帧检测 |
| `_check_updating_state` | 更新状态检查 |
| `_apply_value_map` | 参数写入 |
| `_apply_script_control_params` | Script Control 设参 |
| `_preflight_capture_aspect_ratio` | 预处理比例检查 |
| `preflight_script_control` | 预检 |
| `run_precheck` | 完整预检 |
| `_builtin_calibration` | 内建标定 |
| `ensure_movie_camera_selected` | 切换相机 |

**依赖**：types.py, utils.py
**self 访问**：`self.movie_apphost`, `self.cfg`, `self.output_dir`

---

### Phase 8: evaluate.py (~300 行) — EvaluateMixin

| 方法 | 说明 |
|------|------|
| `evaluate` | 主评估函数（~200 行） |
| `_detect_and_score_boards` | 检测+评分内部循环 |
| `optimize` | optimize() 入口（选择 CD/Bayesian/Hybrid） |

**依赖**：所有 Mixin（最上层）
**self 访问**：所有字段

---

### Phase 9: optimizer_cd.py (~1,500 行) — CoordinateDescentMixin

| 方法 | 说明 |
|------|------|
| `_optimize_coordinate_descent_impl` | 主循环 |
| `_run_single_param_trial` | 单参试验 |
| `_trial_multipliers_for_param` | multiplier 生成 |
| `_strategy_effective_step` | 自适应步长 |
| `_ordered_params_for_iteration` | 参数排序 |
| `_grow_direction` | 方向增长 |
| `_maybe_decay_direction` | 方向衰减 |
| 所有 joint phase 相关逻辑 | |

**依赖**：types.py, detector.py, scoring.py, script_control.py, evaluate.py
**self 访问**：`self.params`, `self.step_decay`, `self.min_improve`, `self.strategy_*`

---

### Phase 10: optimizer_bayesian.py (~500 行) — BayesianOptimizerMixin

| 方法 | 说明 |
|------|------|
| `_optimize_bayesian_impl` | Bayesian 主循环 |
| `_shrink_bayesian_ranges` | 搜索域收缩 |

**依赖**：types.py, evaluate.py
**self 访问**：`self.params`, `self.max_iters`

---

### Phase 11: strategy.py (~400 行) — StrategyMixin (单独文件)

虽小但独立于 CD（被 Orchestration 调用），单独拆。

| 方法 | 说明 |
|------|------|
| `_strategy_active_profile` | 当前探索配置 |
| `_strategize_exploration_profile` | 切换到 aggressive |
| `_update_strategy_state` | 更新步长规模 |
| `_reset_strategy_state` | 重置 |
| `_build_round_strategy_autotune_patch` | 自动调参 |
| `_apply_round_strategy_autotune_patch` | 应用调参 |
| `_maybe_autotune_round_strategy` | 条件调参 |
| `_clamp_strategy_step_scale` | scale 边界 |
| `_strategy_iteration_meta` | 迭代元信息 |

---

### Phase 12: orchestration.py (~1,500 行) — OrchestrationMixin

最大的整合模块。拆成多个子文件也可以，但先放一起因为调用链紧密。

| 分组 | 方法 |
|------|------|
| **Multi-Start** | `_build_multi_start_configs`, `_run_multi_start_campaign`, `_run_single_start` |
| **Round 编排** | `_run_plain_optimize_rounds`, `_run_multi_start_rounds`, `_run_explore_then_refine_rounds`, `_run_explore_then_refine_campaign` |
| **Params Pool** | `_load_pool_entries`, `_write_pool_file`, `_write_camera_best_to_pool`, `_find_best_from_pool`, `_build_patched_config_from_pool_best` |
| **History** | `_write_calibration_summary`, `_write_history_json`, `_load_best_score_history`, `_build_calibration_summary` |
| **Vehicle Writeback** | `_apply_to_vehicle`, `_build_vehicle_param_map`, `on_phase_ended` |
| **旧 multi-legacy** | `_sim_output_root_legacy`, 旧的 _run_* 编排 |
| **Print** | `_print_calibration_summary`, `_print_initial_params`, `_format_duration_stats` |

**依赖**：所有上层 Mixin

---

### Phase 13: cli.py (~300 行)

| 内容 | 说明 |
|------|------|
| `main()` | 主入口 |
| `argparse` 配置 | 所有 CLI 参数 |
| `_configuration_from_args()` | args→cfg |
| `run_standalone_calibration()` | 单次标定 |
| `run_batch_calibrations()` | 批量标定 |

---

### Phase 14: camera_calibration.py 新主文件 (~250 行)

```python
#!/usr/bin/env python3
"""Camera Calibration Module — v1.2.2"""

from calibration.types import *
from calibration.utils import *
from calibration.detector import DetectorMixin
from calibration.scoring import ScoringMixin
from calibration.annotation import AnnotationMixin
from calibration.script_control import ScriptControlMixin
from calibration.evaluate import EvaluateMixin
from calibration.optimizer_cd import CoordinateDescentMixin
from calibration.optimizer_bayesian import BayesianOptimizerMixin
from calibration.strategy import StrategyMixin
from calibration.orchestration import OrchestrationMixin


class CameraCalibrator(
    DetectorMixin,
    ScoringMixin,
    AnnotationMixin,
    ScriptControlMixin,
    EvaluateMixin,
    StrategyMixin,
    CoordinateDescentMixin,
    BayesianOptimizerMixin,
    OrchestrationMixin,
):
    def __init__(self, cfg, params_pool=None):
        # === 所有 self.* 属性的初始化保留在此 ===
        self.cfg = cfg
        self.params_pool = params_pool
        self.boards = []
        self.params = []
        self.real_img = None
        self.real_img_color = None
        self.real_detections = None
        # ... ~100 行初始化保持不变 ...


if __name__ == "__main__":
    from calibration.cli import main
    main()
```

---

## 代码行数估算

| 文件 | 当前行 | 新建行 | 说明 |
|------|--------|--------|------|
| camera_calibration.py | 12,608 | ~250 | 仅保留 __init__ + import |
| calibration/types.py | 0 | ~300 | 新文件 |
| calibration/utils.py | 0 | ~400 | 新文件 |
| calibration/config.py | 0 | ~500 | 新文件 |
| calibration/detector.py | 0 | ~1,200 | 新文件 |
| calibration/scoring.py | 0 | ~800 | 新文件 |
| calibration/annotation.py | 0 | ~600 | 新文件 |
| calibration/script_control.py | 0 | ~900 | 新文件 |
| calibration/evaluate.py | 0 | ~300 | 新文件 |
| calibration/optimizer_cd.py | 0 | ~1,500 | 新文件 |
| calibration/optimizer_bayesian.py | 0 | ~500 | 新文件 |
| calibration/strategy.py | 0 | ~400 | 新文件 |
| calibration/orchestration.py | 0 | ~1,500 | 新文件 |
| calibration/cli.py | 0 | ~300 | 新文件 |
| **总计** | 12,608 | ~9,450 | 净增 ~700 行 (import + class 声明) |

## 实施顺序

```
Phase  1: types.py          ← 无依赖，创建即可验证
Phase  2: utils.py          ← 仅依赖 types
Phase  3: config.py         ← 依赖 types, utils
Phase  4: detector.py       ← 第一个 Mixin，验证 MRO 模式
Phase  5: scoring.py        ← 依赖 detector
Phase  6: annotation.py     ← 依赖 detector, scoring
Phase  7: script_control.py ← 独立 Mixin
Phase  8: evaluate.py       ← 依赖所有下层 Mixin
Phase  9: optimizer_cd.py   ← 最复杂
Phase 10: optimizer_bayesian.py
Phase 11: strategy.py       ← 独立小 Mixin
Phase 12: orchestration.py  ← 最顶层
Phase 13: cli.py            ← 独立模块
Phase 14: 主文件重整 + 删除代码
Phase 15: 删除 clipboard 死代码
Phase 16: 全量回归测试
```

## 注意事项

### 1. self 属性初始化顺序

`__init__` 必须定义所有 Mixin 共享的属性。如果 Mixin 方法在 `__init__` 调用期间被访问，需确保属性已存在。

```python
class CameraCalibrator(DetectorMixin, ...):
    def __init__(self, cfg, params_pool=None):
        # 先定义所有 Mixin 需要的属性
        self.cfg = cfg
        self.real_img = None        # DetectorMixin 会用到
        self.real_detections = None  # AnnotationMixin 会用到
        # 再初始化
        self._load_config()
```

### 2. 循环 import 规避

- `scoring.py` → `detector.py`：无需 import（MRO 自动解析）
- `evaluate.py` → `script_control.py`：同上
- 类型 import：`from calibration.types import BoardProfile` 无循环风险

### 3. `from camera_calibration import *` 兼容

外部脚本可能这样引用：

```python
# camera_calibration.py 末尾添加 re-export
__all__ = [
    # types
    "BoardProfile", "DetectionResult", "BoardScoreDetail",
    # class
    "CameraCalibrator",
    # functions
    "bootstrap_config_from_annotation", "main",
]
```

### 4. import 路径变更

原 `import camera_calibration` → 不变（主文件仍名为 camera_calibration.py）

需更新文件顶部 `sys.path.insert` 逻辑（原行 1-20）。

### 5. 删除死代码

**可安全删除**：
- `_read_clipboard_text` ×2 (L5973, L6105) — 无调用者
- `_set_clipboard_text` ×2 (L6032, L6164) — 无调用者
- `_clear_clipboard_text` ×2 — 无调用者

共 6 个方法定义，影响行数约 120 行。

**需保留**：
- `bootstrap_config_from_annotation` — CLI `--bootstrap-config-from-annotation` 仍在使用
- `_sim_output_root_legacy` — 被 `_run_optimize_loop` 等旧入口调用
- 所有 `_run_*_rounds` 函数 — 活跃使用中

## 验证标准

每次 Phase 完成后：
1. `python camera_calibration.py --help` 不报 import 错误
2. `python -c "from camera_calibration import CameraCalibrator"` 成功
3. 类型检查器不报新错误（pyright/pyflakes）
4. 现有单元测试通过

最终：
1. 全量多相机测试（TRightLeftRearFisheye 三相机同时标定）
2. score 图输出与拆分前完全一致（像素级对比）
3. bayesian 模式正常运行
