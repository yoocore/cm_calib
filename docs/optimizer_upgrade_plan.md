# v1.2.2 优化器全面升级方案

## 总览

| 优先级 | 方案 | 代码位置 | 工作量 | 收益 |
|--------|------|----------|--------|------|
| ★★★ | **P0**: Gauss-Newton 梯度近似 | `coordinate_descent.py` | 中 | 替代盲目 multiplier 扫描，收敛速度翻倍 |
| ★★★ | **P1**: strategy_adaptation 默认启用 | `DEFAULT_CONFIG` | 1行 | 零成本激活已有 step_scale/priority/reorder 机制 |
| ★★☆ | **P2**: Jitter 自适应 | `multi_start.py` | 中 | 自动匹配扰动幅度，无需人工调参 |
| ★★☆ | **P3**: 初始求解器 | `coordinate_descent.py` | 中 | 起步即接近最优 |
| ★★☆ | **P4**: 评分稀疏化 | `strategy.py` | 中 | 省 ~30% trial 时间 |
| ★★☆ | **P5**: Hybrid 两阶段混合 | `hybrid.py` | 中 | CD 快速收敛 + Bayesian 精细搜索 |
| ★☆☆ | **P6**: Multi-start 信息共享 | `multi_start.py` | 小 | 跨 start 传递 step_scale/priority |
| ★☆☆ | **P7**: 参数分组退火 | `strategy.py` | 小 | 分阶段优化，避免参数耦合抖震 |
| ☆☆☆ | **P8**: 抛物线插值 | `strategy.py` | 小 | 仅对 offset 类参数有效 |

## 文件结构

```
calibration/
├── __init__.py
└── optimizer/
    ├── __init__.py          ← OptimizerRegistry: create() 根据 mode 返回 optimizer
    ├── base.py              ← BaseOptimizer 抽象基类
    ├── coordinate_descent.py ← CD + P0(Gauss-Newton) + P3(初始求解器)
    ├── bayesian.py           ← Bayesian + multivariate + n_startup 修正
    ├── hybrid.py             ← P5 两阶段混合
    ├── strategy.py           ← P1(strategy_adaptation) + P4(稀疏化) + P7(退火) + P8(抛物线)
calibration/multi_start.py    ← P2(Jitter) + P6(信息共享)
docs/optimizer_upgrade_plan.md ← 本文件
```

总改动：7 个新文件（~1200 行）+ 原 `camera_calibration.py` 删除 ~400 行 + 新增 ~20 行胶水。

---

## P0: Gauss-Newton 梯度近似 ★★★

### 核心思想

每轮 CD 扫描参数时，你免费收集了雅可比信息：

```
eval(base)              → p_base (所有板所有角点的图像位置)
eval(yaw + Δyaw)        → p_trial
(p_trial - p_base)/Δyaw → ∂corner/∂yaw   (雅可比 J 的第 1 列)
eval(pitch + Δpitch)    → p_trial
(p_trial - p_base)/Δpitch → ∂corner/∂pitch (J 的第 2 列)
...
```

一轮扫完 10 参数 → 完整 J 矩阵。然后解线性最小二乘：

```
Δparams = -(J^T J)⁻¹ J^T · d_base
```

直接跳到预测的最优点——替代 trial_multipliers 的盲目试探。

### 数据结构

```python
@dataclass
class CornerSnapshot:
    param_values: Dict[str, float]
    displacements: Dict[str, np.ndarray]  # board_id → (N,2) sim_points - real_points

class JacobianAccumulator:
    """在线累积图像空间雅可比 J = d(角点位移)/d(参数)"""

    def __init__(self, param_names: List[str]): ...
    def set_base(self, snapshot: CornerSnapshot): ...
    def add_trial(self, param_name: str, delta: float, snapshot: CornerSnapshot): ...
    def gauss_newton_step(self) -> Optional[Dict[str, float]]:
        """解 (J^T J) Δp = -J^T d → 返回 {param: delta}"""

    # ── 内部处理 ──
    # 1. 展平所有 board 的角点位移为长列向量 d (shape: M)
    # 2. 每列 J[:,j] = trial_j 的位移向量 / Δparam_j
    # 3. np.linalg.lstsq(J, -d, rcond=None) → Δparams
    # 4. 每参数 clamp 到 [min_value, max_value]
```

### 改动点

| 代码 | 改动 |
|------|------|
| `evaluate()` 末尾 | 记录 `self._last_corner_snapshot` |
| `TrialResult` | 新增 `corner_displacements` 字段 |
| `_run_single_param_trial` | 返回后通过 dispatcher 更新 JacobianAccumulator |
| `_optimize_coordinate_descent_impl` | 第 1-2 轮正常扫描构造 J；≥3 轮起每轮尝试 GN 一步跳 |

### 触发条件

- 每轮 CD 扫描结束后尝试 GN 步
- GN 步还需验证：apply GN → evaluate → 如果 score 不优于 base 则回退
- 可靠性：GN 步失败次数 ≥ 3 则降级回传统 CD

---

## P1: strategy_adaptation 默认启用 ★★★

**位置**：`DEFAULT_CONFIG` (`camera_calibration.py:~400`)

```python
"strategy_adaptation": {
    "enabled": True,              # False → True（唯一改动）
    "reorder_params": True,
    "adjust_step_scale": True,    # step_scale_up=1.35, step_scale_down=0.85
    "focus_on_joint_candidates": True,
    "bottleneck_board_awareness": True,  # top_k=2, boost=1.25
    # 其余 15+ 字段保持不变
},
```

### 激活后的行为

| 机制 | 效果 |
|------|------|
| `step_scale` | 连续成功 → step×1.35；连续失败 → step×0.85 |
| `priority_score` | 被接受的参数 +2.5 boost；被拒绝的 -0.15 惩罚 |
| `bottleneck_board_awareness` | 找出最差的 2 块板，给影响它们的参数 +1.25x boost |
| `exploration_profiles` | 停滞 2 轮 → expanded (multipliers 扩展)；4 轮 → aggressive |

### 验证

现有 CD 测试通过即可——代码全部已存在，只改 1 行。

---

## P2: Jitter 自适应 ★★☆

### 决策树

```python
def _auto_jitter(param, start_index, campaign_history, pool, step):
    """
    优先级：
      1. campaign 历史 σ ≥ 3 → 1.5 × σ (最准)
      2. _params_pool 跨布局 σ ≥ 3 → 1.5 × σ (较准)
      3. 冷启动 → success/stagnation 启发式
    """
    # ── Primary: campaign 内 σ ──
    if len(campaign_history) >= 3:
        values = [r["final_values"][param.name] for r in campaign_history]
        sigma = np.std(values)
        if sigma > 1e-9:
            return np.clip(1.5 * sigma, 0.3 * step, 6.0 * step)

    # ── Secondary: 跨布局 pool σ ──
    pool_entries = _load_pool_entries_for_camera(camera_name)
    filtered = [
        e["best_params"][param.name]
        for e in pool_entries
        if param.name in e.get("best_params", {})
        and e.get("best_score", float("inf")) < SCORE_THRESHOLD
    ]
    if len(filtered) >= 3:
        sigma = np.std(filtered)
        if sigma > 1e-9:
            return np.clip(1.5 * sigma, 0.3 * step, 6.0 * step)

    # ── Fallback: success/stagnation 启发式 ──
    base = step * 2.0
    if len(campaign_history) >= 2:
        prev = campaign_history[-1]["final_score"]
        prev_prev = campaign_history[-2]["final_score"]
        if prev < prev_prev - 1e-6:
            base *= 0.85   # 改进 → 缩小
        elif abs(prev - prev_prev) < 1e-6:
            base *= 1.4    # 停滞 → 放大
    return np.clip(base, 0.3, 6.0)
```

### CLI/GUI

| 层 | 行为 |
|----|------|
| CLI | `--multi-start-jitter-steps` 默认 `"auto"`，接受数字向后兼容 |
| GUI | `QComboBox`：`auto`（默认 + 推荐）/ `custom 1.0` / `custom 2.0` / `custom 4.0` |
| | 选 `auto` 时 spin 灰掉隐藏；选 `custom` 时 spin 可编辑 |

### 日志

每轮 multi-start 结束写 `multistart_summary.json`：

```json
{
  "jitter_mode": "campaign_sigma",
  "per_param": {
    "mount_yaw": 1.3,
    "mount_pitch": 0.8,
    "lens_fov": 2.4,
    "lens_sensor_offset_x": 0.05
  }
}
```

---

## P3: 初始求解器 ★★☆

### 原理

用 initial evaluate 的角点位移反推参数修正量。5 块锚点板（四角 + 中心）。

```python
class InitialSolver:
    @staticmethod
    def estimate(corner_snapshot, boards, params) -> Dict[str, float]:
        anchors = _select_anchors(boards)  # cb_0, cb_2, cb_4, cb_7, cb_8
        disp = {bid: corner_snapshot.displacements[bid] for bid in anchors}

        # offset → 平均位移
        mean_disp = np.mean([np.mean(d, axis=0) for d in disp.values()], axis=0)
        result = {
            "lens_sensor_offset_x": mean_disp[0],
            "lens_sensor_offset_y": mean_disp[1],
        }

        # fov → 径向缩放
        # avg_radius / delta_radius * fov

        # yaw → 左右不对称
        # (left_mean_x - right_mean_x) / avg_radius * scale_factor

        # pitch → 上下不对称
        # (top_mean_y - bottom_mean_y) / avg_radius * scale_factor

        return result
```

### 触发

在 `_optimize_coordinate_descent_impl` 中 `iter=0`（initial evaluate）之后、第一次 trial 之前：

```python
if self.use_initial_estimator:
    estimates = InitialSolver.estimate(corner_snapshot, self.boards, self.params)
    if estimates:
        for p in self.params:
            if p.name in estimates:
                trial = best_values[p.name] + estimates[p.name]
                trial = np.clip(trial, p.min_value, p.max_value)
                estimates[p.name] = trial
        self._apply_value_map(estimates)
        best_total_detail, best_img = self.evaluate("initial_warm")
```

---

## P4: 评分稀疏化 ★★☆

### 灵敏度矩阵

预计算每个参数对每块板的影响程度：

| 参数 | 影响模式 |
|------|----------|
| `offset_x/y` | 所有板 1.0（均匀平移） |
| `lens_fov` | 0.8 + 0.2 × 归一化半径 |
| `mount_yaw` | 0.1 + 0.9 × 水平偏移（左右板大） |
| `mount_pitch` | 0.1 + 0.9 × 垂直偏移（上下板大） |
| `distortion_k*` | 0.2 + 0.8 × 归一化半径 |

```python
def _build_geometric_sensitivity(boards, params, img_shape):
    """返回 {param_name: {board_id: sensitivity [0,1]}}"""
    center = np.array([img_shape[1]/2, img_shape[0]/2])
    max_r = np.linalg.norm(center)

    sens = {}
    for param in params:
        per_board = {}
        for board in boards:
            cx = board.roi[0] + board.roi[2]/2
            cy = board.roi[1] + board.roi[3]/2
            norm_r = np.linalg.norm([cx - center[0], cy - center[1]]) / max(1, max_r)

            if "offset" in param.name:
                per_board[board.board_id] = 1.0
            elif "fov" in param.name:
                per_board[board.board_id] = 0.8 + 0.2 * norm_r
            elif "yaw" in param.name:
                per_board[board.board_id] = 0.1 + 0.9 * abs(cx - center[0]) / max(1, center[0])
            elif "pitch" in param.name:
                per_board[board.board_id] = 0.1 + 0.9 * abs(cy - center[1]) / max(1, center[1])
            elif "distortion" in param.name or param.name.startswith("lens_distortion"):
                per_board[board.board_id] = 0.2 + 0.8 * norm_r
            else:
                per_board[board.board_id] = 1.0
        sens[param.name] = per_board
    return sens
```

### evaluate() 改动

```python
def evaluate(self, tag, baseline_metrics=None, param_name=None, param_delta=0.0,
             skip_boards=None):
    # 预计算（缓存）
    sensitivity = self._build_geometric_sensitivity(...)
    threshold = 0.3

    if skip_boards is None and param_name is not None:
        skip_boards = {
            bid for bid, s in sensitivity[param_name].items()
            if s < threshold
        }

    for board in self.boards:
        if board.board_id in skip_boards and baseline_metrics:
            base = baseline_metrics.get(board.board_id, {})
            board_scores.append(BoardScoreDetail(
                board_id=board.board_id,
                total_score=base.get("total_score", 0.0),
                rmse=base.get("rmse", 0.0),
                compared=True,
                ...
            ))
            continue
        # 正常 detect + score
```

---

## P5: Hybrid 两阶段混合 ★★☆

### 架构

```python
class HybridOptimizer(BaseOptimizer):
    """
    Phase 1: CD × N 轮 → 快速下到局部最优
    Phase 2: Bayesian × (max_iters - N) trial → 在 best ± 3σ 范围内精巧搜索
    """

    def __init__(self, phase1_iters=15, search_box_sigma=3.0):
        self.phase1_iters = phase1_iters
        self.search_box_sigma = search_box_sigma

    def optimize(self, calibrator):
        # ── Phase 1 ──
        cd = CoordinateDescentOptimizer()
        cd_result = cd.optimize(calibrator, max_iters=self.phase1_iters)

        # ── Phase 2 ──
        bayesian = BayesianOptimizer()
        best_values = cd_result["final_values"]
        bayesian_result = bayesian.optimize_hybrid(
            calibrator,
            warm_start=best_values,
            search_range={
                p.name: (
                    best_values[p.name] - self.search_box_sigma * p.step,
                    best_values[p.name] + self.search_box_sigma * p.step,
                )
                for p in calibrator.params
            },
            n_trials=calibrator.max_iters - self.phase1_iters,
        )

        return bayesian_result if bayesian_result["final_score"] < cd_result["final_score"] else cd_result
```

### Bayesian 改动

```python
class BayesianOptimizer(BaseOptimizer):
    def optimize(self, calibrator):
        sampler = optuna.samplers.TPESampler(
            multivariate=True,                          # ← 新：建模参数协方差
            n_startup_trials=max(10, min(...)),          # ← 新：动态下限
            seed=calibrator.explicit_seed,
        )

    def optimize_hybrid(self, calibrator, warm_start, search_range, n_trials):
        def objective(trial):
            for param in calibrator.params:
                low, high = search_range[param.name]
                trial.suggest_float(param.name, low, high, step=param.step)
            # ... evaluate ...
```

### 入口改动

```python
# optimize() 中
if self.optimizer_mode == "hybrid" or self.optimizer_mode == "auto":
    optimizer = HybridOptimizer()
elif self.optimizer_mode == "coordinate_descent":
    optimizer = CoordinateDescentOptimizer()
elif self.optimizer_mode == "bayesian":
    optimizer = BayesianOptimizer()
```

---

## P6: Multi-start 信息共享 ★☆☆

### 共享状态

```python
@dataclass
class MultiStartSharedState:
    step_scales: Dict[str, float]           # param_name → step_scale
    preferred_directions: Dict[str, float]  # param_name → ±1.0
    priority_scores: Dict[str, float]       # param_name → current priority
    best_per_board_scores: Dict[str, float] # board_id → best ever score

    def merge(self, start_state: dict):
        """指数移动平均合并另一个 start 的状态"""
        alpha = 0.3
        for name, scale in start_state.get("step_scales", {}).items():
            self.step_scales[name] = (
                alpha * scale + (1 - alpha) * self.step_scales.get(name, scale)
            )
        # 同理合并 priority_scores / preferred_directions
```

### 流程

```python
def _run_multi_start_campaign(config_path, cfg, run_cfgs):
    shared = MultiStartSharedState()

    for i, run_cfg in enumerate(run_cfgs):
        if i > 0:
            run_cfg["warm_start_state"] = dataclasses.asdict(shared)

        result = _run_single_start(run_cfg)
        shared.merge(result.get("optimizer_state", {}))
        results.append(result)

    return results
```

---

## P7: 参数分组退火 ★☆☆

默认关闭，仅参数耦合严重的场景启用。

```python
# camera_calibration.py DEFAULT_CONFIG
"curriculum": {
    "enabled": False,
    "phases": [
        { "progress_max": 0.50, "active_params": [
            "lens_fov", "lens_sensor_offset_x", "lens_sensor_offset_y"
        ]},
        { "progress_max": 0.80, "active_params": [
            "lens_fov", "lens_sensor_offset_x", "lens_sensor_offset_y",
            "mount_yaw", "mount_pitch"
        ]},
        { "progress_max": 1.00, "active_params": None },  # 全部
    ],
}
```

```python
def _ordered_params_for_iteration(self):
    params = super()._ordered_params_for_iteration()
    if not self.curriculum_enabled:
        return params
    progress = self._total_iteration_count / max(1, self.max_iters)
    for phase in self.curriculum_phases:
        if progress <= phase["progress_max"]:
            active = phase.get("active_params")
            return params if active is None else [p for p in params if p.name in active]
    return params
```

---

## P8: 抛物线插值 ★☆☆

仅对 offset 类参数，默认关闭。

```python
"parabolic_interpolation": {
    "enabled": False,
    "params": ["lens_sensor_offset_x", "lens_sensor_offset_y"],
}
```

```python
def _parabolic_optimal_offset(self, param, base_value, base_score, step, direction, evaluate_fn):
    # 跑 3 点: 0, step, 2×step
    points = [(0.0, base_score)]
    for mult in [1.0, 2.0]:
        trial = base_value + direction * step * mult
        score = evaluate_fn({param.name: trial})
        points.append((mult, score))

    # y = ax² + bx + c
    a, b, _ = np.polyfit([x for x, _ in points], [y for _, y in points], 2)
    if a <= 0.0:
        return None  # 非凸，不跳

    optimal_mult = -b / (2.0 * a)
    if not (0.5 <= optimal_mult <= 3.0):
        return None  # 离探测范围太远

    return base_value + direction * step * optimal_mult
```

---

## 实施步骤

```
Step  1: 创建 calibration/目录 + __init__.py + optimizer/__init__.py + base.py
Step  2: 抽取 coordinate_descent.py（含 P0/P3 骨架）
Step  3: 抽取 bayesian.py（含 multivariate 修正）
Step  4: 抽取 calibration/multi_start.py
Step  5: camera_calibration.py optimize() 改为注册表模式
Step  6: P1: strategy_adaptation_enabled = True
Step  7: P0: JacobianAccumulator + CornerSnapshot + GN 步
Step  8: P2: Jitter 自适应决策树 + CLI/GUI
Step  9: P3: InitialSolver 初始求解器
Step 10: P4: 灵敏度矩阵 + 稀疏 evaluate
Step 11: P5: HybridOptimizer + Bayesian.optimize_hybrid
Step 12: P6: MultiStartSharedState + 指数移动平均合并
Step 13: P7: 退火 + P8: 抛物线（默认关闭）
Step 14: 全量回归测试 + performance 对比
```
