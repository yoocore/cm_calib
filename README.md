相机标定循环（手动前置条件）

用途
- 默认你已经手动完成了这些前置操作：
  - 打开 IPGMovie
  - 切换到目标摄像头视角
  - 打开 Camera Settings，并至少进入一次 lens 页面，确保 `.camera.cammoddlg` 相关控件已经创建
- 在此前提下，这个脚本只负责：
  - 通过 Script Control DDE 下发安装参数
  - 通过 FBO 抓取 IPGMovie 离屏图像
  - 在仿真图和真实图中检测多个标定板
  - 按多板几何聚合得分进行比较
  - 迭代搜索，直到得分达到可接受水平

文件说明
- camera_calibration.py：主脚本
- configs/camera.<camera>.json：每个摄像头维护一份运行输入配置，例如 configs/camera.rear_tv.json
- configs/bootstrap.template.json：独立的 bootstrap 模板输入配置，用于在第一个摄像头还没有现成 camera config 时生成新配置；文件里尽量只保留项目相关的板原型和少量覆盖项
- script_control_apply.tcl：唯一维护的 Script Control 命令脚本
- project_notes/ipgmovie_control_workflow.md：IPG-MOVIE 控制流程、菜单项映射、刷新行为与最小化行为的持续记录文档
- project_notes/README.md：项目强相关知识文档索引，收纳长期笔记、设计文档、流程文档和依赖清单

项目知识文档
- `project_notes/` 目录用于存放与当前 CameraCalibration 工程强相关的长期记录和正式文档，例如 GUI 设计蓝图、运行健康基线、设计说明、流程记录与依赖清单。
- 这些文档与 `/memories/repo/` 下的 repo memory 互为补充：memory 方便代理长期记忆，项目目录内文档方便版本管理、共享和人工检索。

依赖
- Python 3.9+
- 使用当前维护的本地依赖文件安装：
  - 在仓库根目录执行：python -m pip install -r Data/Script/CameraCalibration/project_notes/requirements.txt
  - 在 Data/Script/CameraCalibration 目录执行：python -m pip install -r project_notes/requirements.txt

解释器与环境
- 维护中的依赖文件位于 Data/Script/CameraCalibration/project_notes/requirements.txt。
- 当前工作区推荐使用项目本地虚拟环境 .venv/Scripts/python.exe。
- 在 VS Code 中，选择一次工作区解释器后，固定到 .venv 即可。
- 如果 cv2、numpy、PIL 等导入突然再次显示未解析，优先先检查当前解释器是否正确，而不是直接排查脚本本身。
- 手工运行命令时，优先使用明确的解释器路径：
  - c:/CM_Projects/CMO141_Calibration/.venv/Scripts/python.exe camera_calibration.py --config configs/camera.rear_tv.json

推荐运行环境
- Windows 缩放 100%
- 显示器布局与分辨率固定
- 保持 IPGMovie 在线且可响应
- CarMaker 与 IPG-MOVIE 的 DDE 必须可用
- lens 页面至少初始化过一次后，Script Control、Camera Settings、IPGMovie 窗口在参数写入和 FBO 抓图烟测期间可以保持最小化

快速开始
1. 编辑对应摄像头的配置文件，例如 configs/camera.rear_tv.json。
2. 创建或刷新本地虚拟环境，并根据 project_notes/requirements.txt 安装依赖。
3. 在 VS Code 中确认当前选择的解释器是 .venv/Scripts/python.exe。
4. 设置 real_image 和 output_dir。
5. 为所有可见标定板配置 boards[]。
6. 确认当前 Script Control 命令路径指向 Data/Script/CameraCalibration/script_control_apply.tcl。
7. 可选：把当前 Script Control 读到的值回写进配置：
  python camera_calibration.py --config configs/camera.rear_tv.json --capture-initials --write-initials-to-config
8. 使用配置中的初始值执行默认优化：
  python camera_calibration.py --config configs/camera.rear_tv.json
9. 可选：如果想测试是否存在另一个邻近 basin，可运行 multi-start 短跑探索：
  python camera_calibration.py --config configs/camera.rear_tv.json --multi-start-count 4 --multi-start-iters 24 --multi-start-jitter-steps 2.0
10. 可选：如果想先短跑探索，再从最优起点做长跑收束，可以执行一体化 campaign：
  python camera_calibration.py --config configs/camera.rear_tv.json --explore-then-refine --multi-start-count 4 --multi-start-iters 24 --refine-iters 180
11. 成功完成的优化会自动把 best_values 回写到配置中的 initial 字段，作为下一次运行的初始值。

可选行为说明
- 始终显式传入 --config，例如 --config configs/camera.rear_tv.json。
- 后续新增更多视角时，继续沿用 configs/camera.<camera>.json 命名方式，例如 configs/camera.front_tv.json。
- --resume-from-result 仍可作为遗留/手动恢复模式使用，但已不再是推荐的默认工作流。
- --explore-then-refine 会使用 --multi-start-* 作为探索阶段参数，然后从短跑阶段最优结果启动一次 refine。
- 在 --explore-then-refine 模式下，如果省略 --multi-start-count，默认使用 4 个起点；如果省略 --multi-start-iters，默认使用 min(config max_iters, 24)。
- 默认单次运行、multi-start、explore-then-refine 三种模式，都会把最终 best_values 持久化回输入 config，作为下一轮 initial 值。
- 如果你想在任何优化前，先把当前 IPG-MOVIE 里的值同步回配置，--capture-initials --write-initials-to-config 仍然很有用。

简化后的仓库布局
- 仓库现在对每个视角只保留一个摄像头专属 JSON 配置，命名为 configs/camera.<camera>.json。
- 这些版本化输入统一存放在 Data/Script/CameraCalibration/configs/ 下。
- 仓库只保留一个维护中的 Script Control Tcl：Data/Script/CameraCalibration/script_control_apply.tcl。
- Python 路径已经有意收敛为：Script Control DDE 负责参数写入，IPG-MOVIE dde_fbo 负责图像抓取。
- 仓库不再维护 UI 窗口连接逻辑，也不再维护其它 movie 抓取模式；当前有效路径就是纯 DDE/FBO。
- 旧的 overnight/best/final/proposed 配置变体已经有意从版本化输入中移除。

按当前视角自动提议 boards 配置
- 你可以根据当前 real_image 自动生成一份候选 boards 配置：
  python camera_calibration.py --config configs/camera.rear_tv.json --propose-boards
- 该命令会输出：
  - 一份与输入 config 相邻的提议配置：*.proposed.json
  - 一张带编号的预览图：output_dir/board_proposal_preview.png
- 当前行为：
  - custom_maker 会按单实例提议；旧的 custom_groundmaker 仍兼容
  - checkerboard 会按当前视角中可见的 7x3 候选重复实例提议
  - 如果多个逻辑板族共享同样的 checkerboard size，目前提议结果会输出通用 checkerboard 实例，仍然需要人工快速确认一次

基于人工标注图引导生成新相机配置
- 如果某个新摄像头视角已经具备：
  - 真实图片，例如 Movie/ngxpro/8_left_tv_origin.jpg
  - 一张用红框标出可见标定板的人工标注图片
- 可以直接基于独立模板 config 生成新的配置，而不依赖已有 camera json：
  python camera_calibration.py --bootstrap-config-from-annotation --bootstrap-template-config configs/bootstrap.template.json --bootstrap-real-image C:/CM_Projects/CMO141_Calibration/Movie/ngxpro/8_left_tv_origin.jpg --bootstrap-annotated-image C:/CM_Projects/CMO141_Calibration/Movie/ngxpro/8_left_tv.jpg
- 默认行为：
  - 先使用代码内置的稳定默认配置，再叠加 bootstrap 模板里的项目相关覆盖项
  - 用 --bootstrap-real-image 替换 real_image
  - 从 --bootstrap-annotated-image 中提取红框作为 board ROI
  - 用 OCR 从标注图里读取每块板的真实标签文字，并把识别结果写入最终 board_id
  - 对第一次未识别成功的标注框，脚本会优先在该框内部做局部 OCR，然后才回退到更大的补充裁剪区域，避免大框把其它标签一起带入后干扰识别
  - 对参考图里无法按完整棋盘检测到的 checkerboard，自动裁出当前可见 ROI 模板，并切换到 template_match fallback
  - 对 bootstrap 生成的 custom_maker，configs/bootstrap.template.json 里不需要手工提供 template_image；脚本会默认直接使用整块人工标注 ROI 生成 template_image，并把 template_image、template_source_roi、template_source_crop 一起写入最终 configs/camera.<camera>.json，方便人工追溯；不会再默认从 ROI 里二次自动猜一个更小的局部纹理块
  - 通过 Script Control 读取当前激活的 IPG-MOVIE 相机窗口参数，并写入 parameters.*.initial
  - 在模板输入同目录写出一份运行配置，例如 configs/camera.left_tv.json
  - 在 SimOutput/<camera>/annotation_bootstrap_preview.png 写出一张校验预览图
- 标注图建议：
  - 每块板使用单独闭合的红框，框与框不要相交、不要共边、不要通过文字笔画间接连在一起
  - 标签文字放在各自红框内部，且只属于当前板，不要跨到相邻框或框外
  - 标签尽量使用清晰的黑色粗体字，字号明显大于场景纹理细节；优先使用 B1、S2、C4 这种纯字母加数字形式
  - 标签与红框边线、板内纹理、相邻标签之间尽量留出可见空白，减少 OCR 在大范围补充裁剪时被其它元素干扰
- 当前建议：
  - bootstrap 模板里只保留会因项目变化而变化的板原型信息，例如 board_type、board_size、template_image、custom_detector、weight
  - parameters、优化顺序、Script Control 路径、acceptance 默认值等稳定结构由代码自动补齐并写入最终生成的 camera config
  - G1 模板项里的 board.board_id 现在只用于区分 left/center/right 三种 groundmaker 原型，最终输出 board_id 仍以标注图 OCR 结果为准
  - 自动生成的局部 checkerboard 模板会写到 real_image 同级目录下的 bootstrap_templates/<camera>/ 中
  - bootstrap 依赖当前 Python 环境可导入 rapidocr-onnxruntime
- 可选覆盖项：
  - --bootstrap-template-config：指定 bootstrap 模板输入路径；如果省略，默认使用脚本同目录下的 configs/bootstrap.template.json
  - --bootstrap-output：指定生成 JSON 的输出路径
  - --bootstrap-preview：指定预览图输出路径
  - --bootstrap-camera-name：当目标 camera 名称不想从图片文件名推导时可手动覆盖
  - --bootstrap-skip-current-params：如果只想做 ROI/template bootstrap，不想读取当前窗口参数，可以显式跳过
- bootstrap 模板回归检查：
  - 如果想快速确认 bootstrap 生成的 custom_maker template 没有意外缩成一小块纹理，可以执行：
    c:/CM_Projects/CMO141_Calibration/.venv/Scripts/python.exe bootstrap_template_health_check.py --config configs/camera.right_rear.json
  - 该检查会重点验证：
    - template_source_crop 是否异常小于 template_source_roi
    - bootstrap_templates/<camera>/ 下的自动模板图片尺寸是否与配置一致
    - bootstrap 生成的 custom_maker 是否仍然保持“整块人工 ROI 作为模板来源”的约束
  - 检查结果会写到 SimOutput/bootstrap_template_health/<timestamp>/summary.json

输出结果
- 截图：output_dir/*.png
- 最优得分图：每当出现新的全局最优，脚本都会额外在旁边写出一个带评分覆盖层的 *_score.png
- 优化结果：output_dir/result.json
- result.json 现在包含 acceptance 字段，用于记录该次标定是通过 target_score 达标，还是通过瓶颈兜底阈值判定通过

评分说明
- 分数越低表示匹配越好。
- 当前实现支持：
  - 命名 checkerboard 板，例如 B1-B4、S1-S5
  - custom_maker 板，例如 G1_left、G1_center、G1_right
- 每块板都会独立计算 RMSE、max_error、miss_rate。
- 最终总分是各板加权和，再叠加退化惩罚。
- 如果关键板退化过大，该次试探会被拒绝。
- config 中的 target_score 控制停止阈值。
- 最终 acceptance 会在优化结束后统一评估：
  - 如果 best_score <= target_score，直接判定通过
  - 如果没有达到 target_score，但优化已经表现出明显瓶颈，只要 compared-board 的最大分数 < acceptance_criteria.bottleneck_board_score_max_threshold 且平均分数 < acceptance_criteria.bottleneck_board_score_avg_threshold，仍可判定通过
  - 如果两类条件都不满足，则该轮运行会在 result.json 和 campaign summary 中标记为未通过

优化说明
- 主循环里，每个参数都已经执行单参数双向探测。
- joint_exploration 默认更保守，只作用于 joint_exploration.param_names。
- 如果某个摄像头更适合对所有参数都做联合探索，可以把 joint_exploration.apply_to_all_params 设为 true。
- apply_to_all_params 不会改掉当前“按选定参数保守处理”的默认行为，因此像 rear_tv 这种视角特定配置仍可保持谨慎。
- 如果希望脚本在不同摄像头上自动调整“先搜谁、搜多大、哪些方向值得继续关注”，可以增加可选的 strategy_adaptation：
  {
    "strategy_adaptation": {
      "enabled": true,
      "reorder_params": true,
      "adjust_step_scale": true,
      "focus_on_joint_candidates": true,
      "bottleneck_board_awareness": true
    }
  }
- 这层自适应不会改 acceptance 语义，也不会改参数硬边界；它只会根据已接受 move 和 joint candidate 历史，动态调整参数遍历顺序与运行态有效步长。
- bottleneck_board_awareness 打开后，脚本会额外观察“当前最差的几块板”有没有被某个参数持续改善；即使 total score 还没立刻变好，这个参数也会被提到更前面继续试。
- exploration_profiles 会按 stagnation_count 自动切换探索档位。默认会从 baseline 逐步切到 expanded、aggressive，对单参和 joint 试探追加更大的 trial multiplier，而不是一直卡在同一个半径里原地磨。
- enabled=false 时行为与旧版本一致；建议先在新摄像头的短跑 explore 或 explore-then-refine 上启用，再决定是否作为正式长跑基线。

boards[] 配置说明
- 每个 board 条目都必须包含：
  - board_id
  - board_type
  - critical
  - weight
  - roi
- checkerboard 还必须包含 board_size = [cols, rows]，表示内角点数量
- custom_maker 不需要在 configs/bootstrap.template.json 里手工写 template_image；脚本会基于 real_image 和 roi 自动裁出模板并写到当前 camera 独立目录，且 bootstrap 生成的 configs/camera.<camera>.json 会显式保留这个 template_image 路径
- 如果 custom_maker 来自 bootstrap 标注图，默认会把整块 roi 当作 template_source_roi，并把 template_source_crop 设为整块 roi；如果后续确实需要更小的模板来源，再显式手工覆盖 template_source_crop
- degrade_threshold_* 字段用于定义每块板的防退化保护阈值
- 脚本在评分前会先把截图缩放到参考图尺寸
- 参考图必须能检测到所有关键板

限制
- lens 参数仍然依赖 IPG-MOVIE 内部存在 `.camera.cammoddlg`；因此目前仍要求至少打开过一次 lens 页面。
- 如果某些参数在 IPGMovie 中不是实时生效的，收敛效果会很差。
- custom_maker 默认使用 template_match，并在运行前自动生成当前 camera 专属模板；旧的 custom_groundmaker 仍兼容原有 ORB/template 路径。
- ChArUco/Aruco 只在设计文档里描述过，脚本里尚未实现。

提示
- 另外还有一个用于切换相机的 Tcl 示例，位于：
  Data/Script/Examples/RemoteControlIPGMovie.tcl
- 如果需要，可以把它和当前这套 RPA 流程结合使用。
