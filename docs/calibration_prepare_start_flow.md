# CM Prepare / Start 流程修复

## 问题

标定结束后 `_write_best_values_to_vehicle_config` 将 best params 写入了 vehicle 文件；但下次点击 Start 时，没有重新 start/stop testrun，simulation 内存中的 Sensor 值仍是旧的，导致 `capture_initial_values` 读到旧值，初始 score = 400+。

## Root Cause

`_reuse_existing_runtime_for_camera`（Start 路径）跳过了 testrun restart，直接走 sync → abraxas → capture。

`_prepare_runtime_for_camera`（Prepare 路径）有 `bootstrap_testrun_for_movie_via_cmapi_sync`，但夹杂了 sensor activation 和 capture，不符合"准备"的语义。

## 目标流程

### CM Prepare（`_prepare_runtime_for_camera`）

| Step | 操作 | 说明 |
|------|------|------|
| 1 | Kill all CarMaker/Movie 进程 | 确保干净环境 |
| 2 | Start CarMaker | 启动 CM 进程 |
| 3 | Bootstrap testrun（start → wait running → stop） | 加载 vehicle 文件默认值到内存 |
| 4 | Start GUI Movie | 启动 IPG-MOVIE |
| 5 | Wait movie scene ready | Movie 画面就绪 |
| 6 | Setup movie：abraxas → camera selection → widgets → view size | IPGMovie 画面/镜头配置 |

**明确不做的事：**
- 不 activate sensor（Prepare 只管环境，不选 sensor）
- 不 capture_initial_values（没有 sensor 激活就不该 capture）

### Start（`_reuse_existing_runtime_for_camera`）

| Step | 操作 | 说明 |
|------|------|------|
| 1 | Check CarMaker + Movie 进程是否完整 | health check |
| 2 | 如果不完整 → kill all → 走 Prepare 流程 | 自动恢复 |
| 3 | Activate sensor（写 `Sensor.X.Active = 1`） | 选择要标的 camera |
| 4 | Testrun restart（start → wait running → stop） | **加载最新 vehicle 文件**（含 writeback 值） |
| 5 | sync_gui testrun selection | 同步 GUI TestRun 选择 |
| 6 | abraxas → camera selection → widgets | IPGMovie 配置 |
| 7 | capture_initial_values | 从 simulation 内存读初始参数（此时已是最新） |
| 8 | Begin calibration | 开始标定 |

## 改动文件

- `src/orchestration/calibration_orchestrator.py`：`_prepare_runtime_for_camera` 和 `_reuse_existing_runtime_for_camera`

## 改动内容

### `_prepare_runtime_for_camera`

1. 删除条件性启动逻辑→**始终 kill all 再重来**
2. 删除 `activate_single_vehicle_sensor`
3. 删除 `capture_initial_values_to_config`

### `_reuse_existing_runtime_for_camera`

1. 确保 `bootstrap_testrun_for_movie_via_cmapi_sync` 在 sensor activation 之后、capture 之前被调用
2. 保持异常处理逻辑不变（FBO 检测 → kill → re-prepare 已在）
