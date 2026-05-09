- 当前项目单摄像头切换的正确落点是 Vehicle infofile 的 `Sensor.xx.Active`，不是 TestRun。本轮已实现控制脚本侧规则：先由 TestRun 解析 `Vehicle`，再按传入 sensor 名或 IPG-MOVIE label `CAMERA_RSI-SENSOR Vhcl.<name>` 匹配到 `Sensor.xx.name`，将该实例 Active=1，其余置 0。
- 运行前若需重置环境，清理范围应覆盖 `CarMaker.win64.exe` 和 `Movie.exe`，包括 GUI Movie 与 GPUSensor Movie 进程。
- `cmapi_testrun_control.py` 的 CarMaker 单实例复用现在必须先校验运行态 projectdir：通过 DDE RunScript 在 CarMaker Tcl 中执行 `pwd` 读取当前工程目录；只有与目标 `project_root` 一致时才复用，否则视为复用校验失败。启新实例时显式传 `-projectdir <project_root>`，若允许 cleanup，则在校验失败时清空现有 CarMaker/Movie 栈并按目标工程重启。

- overlay_residual 模式下，custom_groundmaker 检测不能直接用 residual 图做 ORB/template 匹配；应在 direct prepared image 上检测，再用 residual 链路做整体评分。
- rear_tv overnight config uses groundmaker ROI [100, 650, 1600, 500] so the reference image reliably detects the floor marker.
- Overnight unattended run writes progress into SimOutput/rpa_calib_overnight/result.json from iter 0 onward; terminal session ID used for the current run was 4a94903e-513f-4e02-9637-29139e2b7d9d.

- rear_tv truth config with 9 checkerboards works in direct mode and detects 9/9 on both reference and current sim frame; the same set drops to 2/9 in overlay_residual, so remove Movie overlay before running that truth config.
- rear_tv G1 left/right coarse patches had persistent ~10 px translation error; refining them to smaller templates (left 385,835,70,50 and right 1425,835,70,50) reduced left/right RMSE to about 8 px and improved the G1-focused refine run.

- CarMaker Camera Settings 的 TkChild 坐标输入框在 win32 backend 下可用 SetForegroundWindow/SetFocus + send_keys 做无鼠标读写；pywinauto 的 set_focus 会导致鼠标被挪动。
- TkChild 坐标框支持 WM_GETTEXT/WM_SETTEXT；再补 WM_KEYDOWN/WM_KEYUP 的 VK_RETURN 可提交修改。用这条 message 路径时，前台窗口、鼠标位置、剪贴板都可保持不变。

- 已验证可用的无 Camera Settings 键鼠写值路径是 Python -> Script Control Start -> send IPG-MOVIE -> .camera widget；当前映射为 roll=.camera.presetFrame.x, pitch=.camera.presetFrame.y, yaw=.camera.presetFrame.z, pos_x/y/z=.camera.presetFrame.evptx/evpty/evptz，读回用 svptx/svpty/svptz 更稳定。当前 run config 的 Script Control 窗口标题为 CarMaker Office - Script Control，Start 相对坐标是 (912, 647)。
- Script Control 模式下 pos_x/pos_y/pos_z 读回走 svptx/svpty/svptz，实际只有 3 位小数精度；校验时必须按 3 位小数比较，否则像 0.6398 会被读成 0.640 导致误判失败。

- Script Control 首次装载 runtime Tcl 的稳定路径是：若文件不存在则先创建占位 Tcl，然后点击 Open，在 Browser 顶部 TkChild 路径框用 WM_SETTEXT 写入相对路径，再点 OK；不要走 New，也不要依赖底部 console 输入。Browser 弹出等待和装载完成等待要分开计时，否则 Browser 弹出较慢时会误判超时。
- fast_refine / run config 的 script_control_script_path 常指向 SimOutput 下的 Tcl；如果代码只接受 Data/Script 子路径并静默回退到默认 runtime Tcl，会在 iter 0 后等待错误的 result file 而超时。应保留配置路径；位于 Data/Script 内时传相对路径给 Browser，其他位置传绝对路径。
- 标注导出若直接把 eval/prepared 图坐标画回原截图，会出现框和文字错位；应按 _prepare_eval_image 的缩放与居中偏移把检测点反投回源图后再绘制。
- IPGMovie Camera Lens Parameters 已通过 Script Control Start 路径验证可读写并可恢复：lens_fov=.camera.cammoddlg.fov.e（滑条 90..250, resolution 0.1），lens_scale=.camera.cammoddlg.fisheye.ctrl.e1（0.5..1.5, 0.005），lens_offset_x=.camera.cammoddlg.fisheye.ctrl.e2（-0.25..0.25, 0.01），lens_offset_y=.camera.cammoddlg.fisheye.ctrl.e3（-0.25..0.25, 0.01）。
- truth-overall 优化器现支持 priority_board_acceptance：可为指定板（当前可用于 G1_left/G1_center/G1_right）设置 min_board_score_improvement 与 max_total_score_worsen；当目标板明显变好且整体恶化未超限时，可绕过纯 total_score 改善门槛。
- Script Control start 路径现先尝试对解析出的 Start 按钮发送 BM_CLICK，无需鼠标移动；若当前会话里后台触发无效，再回退到物理点击，保证运行不中断。
- 标定实现目录已从 Data/Script/RPA 重命名为 Data/Script/CameraCalibration，以覆盖当前职责范围。旧的 overnight/best/final/proposed JSON 变体已移除；当前主脚本为 Data/Script/CameraCalibration/camera_calibration.py，按 camera 保留独立配置文件 config.<camera>.json；活动 Script Control 命令脚本为 Data/Script/CameraCalibration/script_control_apply.tcl，runtime wrapper 为 Data/Script/CameraCalibration/script_control_runtime.tcl，二者都收敛在该目录下；runtime wrapper 仍由脚本自动生成，不作为手工维护入口。
- Script Control 的主/左侧 TkChild 文本区可在后台通过 WM_SETTEXT 改写为新的 RunScript 命令，但在保持其他窗口前台时，WM_KEYDOWN/WM_CHAR 的 Enter、UIA invoke、MSAA accDoDefaultAction 都不会真正执行该命令；只有前台交互式 click_input/物理点击会触发 Start。
- 已验证真正的零打断 Script Control 执行入口：通过 pywin32 DDE 连接 service=`TclEval`、topic=`CarMaker`，直接执行 `RunScript {C:/.../Data/Script/CameraCalibration/script_control_apply.tcl}` 或其他 Tcl 脚本。此路径会更新结果文件且不会切换前台窗口；对 `script_control_console_probe.tcl` 与当前单一入口 Tcl 都已验证成功。

- rear_tv 当前主代码已接入 DDE 优先 Script Control 执行；在保持 script_control_execute_mode=`start` 的配置下，真实 1 轮 smoke 可正常跑完并回到 best_score=30.001348。当前 optimize 入口仍要求可连接的 IPGMovie online 窗口；Script Control 窗口是否需要则取决于执行模式，外部 Camera Settings 窗口已不再是主流程前置条件。
- 2026-04-29 验证更新：rear_tv 的 connect_windows() 已不再连接外部 Camera Settings 窗口，故 settings_window_title_re 对主流程已无影响；但 Script Control 写参脚本仍直接访问 `.camera.cammoddlg.*` widget。把 script_control_execute_mode 直接切成 `dde` 会在当前环境报 `missing widget .camera.cammoddlg.fov.e`，说明“无 Script Control 窗口依赖”不等于“无 Camera widget/dialog 依赖”，因此默认执行模式仍应保留 `start`，除非后续先补上自动打开/确保 `.camera.cammoddlg` 存在的 Tcl 路径。

- rear_tv 1 轮 smoke 的前台跟踪显示：DDE 写参阶段不会切前台；真正打断用户输入的是 capture_movie 前的 _prepare_movie_window_for_capture，它会在约 36s 时把 IPGMovie 拉到前台，并持续到本轮截图/评分结束后才还回原窗口。

- IPGMovie 直接导图方向已缩小到 Tcl/OpenGL 链：`SaveImage_export` 只是编码器，签名为 `FName Format Width Height Data`，不能自己抓当前视角。当前更有希望的无窗口导图入口是 `send IPG-MOVIE { gl readpixels x y image }`：已验证其用法是 `gl readpixels 0 0 probeImg`，并可把结果写成 png；但在当前探针下导出的图像仍是全黑，说明 readback 目标缓冲区/时机还没对上。

- `movie_capture_mode='dde_fbo'` 已在主代码中实现并验证：单次 `capture_movie()` 不会把前台切到 IPGMovie。当前实现通过 `send IPG-MOVIE` 在进程内创建离屏 FBO，重放 `FBO.DistCL` 显示列表，再用 `gl bindframebuffer_read + gl readpixels` 写出 png。
- 直接读默认 framebuffer 仍是纯黑；直接读 `View($vno).FBO` 能拿到非黑但不完整的原始帧；重放 `FBO.DistCL` 后得到的是非黑、完整的鱼眼圆形成像，但当前内容看起来像车体俯视鱼眼，不是 rear_tv 标定板视图，因此分数仍在约 1.04e7 量级。
- 试图把同样的取图逻辑改发到 `GPUSensor_1_0` 解释器时，`send GPUSensor_1_0 {...}` 通过 CarMaker DDE 执行会失败；当前已验证可工作的解释器仍是 `IPG-MOVIE`。
- 关键根因已确认：当前 IPGMovie 窗口屏幕上显示的 rear_tv 仿真板图本身是正确的，但早先的 `dde_fbo` 实现只去读 `View(FBO.tex)` / `FBO.DistCL` 中间结果，因此拿到的是错误的图形上下文，不是窗口最终显示帧。
- 可工作的无抢前台导图路径不是“手工重放 `FBO.tex`”，而是 `send IPG-MOVIE` 中先创建外层 `captureFBO`，再在该 FBO 作用域里直接执行 `UpdateView $vno`，让 IPGMovie 自己把最终 fisheye 结果画到当前 framebuffer，之后再 `gl bindframebuffer_read $captureFBO ; gl readpixels` 导出。
- 这条 `captureFBO -> UpdateView -> readpixels` 路径已验证三件事：1) 导出的离屏图是正确的 rear_tv 仿真板图；2) 用现有评分器打分约为 `32.245`，与旧窗口截图的 `30.272` 同量级，远优于错误上下文的约 `1.04e7`；3) 前台跟踪显示执行期间未切走用户当前窗口。
- `movie_content_crop` 现在用于窗口抓图路径（如 `client`），支持 `right=0` / `bottom<=0` 这类相对右下角偏移；`dde_fbo` 默认不再复用这组裁剪，除非显式配置 `movie_dde_content_crop`，否则会保留离屏导图原始内容。
- 当前 rear_tv 配置的低风险备用路径已固定为窗口 client 区截图加 `movie_content_crop=[0, 27, 0, -23]`，可稳定裁成 `1250x1000`；新版 `dde_fbo` 切到 `captureFBO -> UpdateView -> readpixels` 后，单次 evaluate 仍保持约 `32.245` 分数，且未主动切前台。

- rear_tv 若要从当前窗口里的手调状态继续优化，不能用 `--resume-from-result`，因为它会把参数重新覆盖成 output_dir/result.json 里的旧 best_values。应先把当前手调值写回活动 config 的 initial，再直接用 `--config camera_rpa_config.rear_tv.json` 启动新一轮。
- 2026-04-29 当前 CameraCalibration 主线中，`run.log` / `continue_resume.log` 只要进入 optimize 主流程就会自动落到 output_dir；不再仅依赖 CLI 入口。DDE size probe 与每轮 movie capture 的临时 `*.tcl` / `*.txt` 在成功后会自动删除，失败时保留供排障。
- isolated output_dir 模式下，resume 不能在写入本轮 starting marker 之后再回读 marker，否则会把上一次 result_json 覆盖丢失；应先缓存上一次恢复路径。若 marker 缺少 result_json，优先回退到 marker.output_dir/result.json，再回退到同前缀最新的隔离结果目录。
- 整理已落盘的 calibration 产物时，若要重命名 `initial.png` / `*_score.png` 之类文件，不能只做 JSON 原始文本替换；Windows 路径在 JSON 里带转义，必须按结构化 JSON 递归改写字符串字段，否则文件名改了但 result.json / campaign_summary.json 仍会残留旧引用。
- left_tv 多起点 campaign 若所有 run 共享同一个 `script_control_result_path`，会因 `script_control_camera_apply_result.txt` 文件锁导致除首个 run 外全部以 WinError 32 失败；已在 `camera_calibration.py` 中改为按每个 run/output_dir 生成独立 result 文件，explore/start_xx 与 refine 都要隔离。

- 2026-05-04 left_tv 从约 31.68 平台进入 28 分段的有效策略不是单纯重复 formal campaign，而是把 `priority_board_acceptance` 更偏向 `S3 -> G1_center -> S2`，放宽 `max_total_score_worsen` 到约 4.5，并把搜索顺序前移到 `pitch, yaw, lens_fov, pos_z`。短验证显示首个有效入口是 pitch 正向（24.7485 -> 24.8085），随后配合 yaw/pos_x/pos_y/pos_z 的新 basin 可把 best 压到约 29.30，再进一步到约 28.42；但 `pos_z` 与 `pitch` 的 4x/6x 很容易触发 critical degrade，说明应保留宽搜索方向，但继续正式长跑时要重点观察这两个参数的大步长是否过于激进。
- 2026-05-04 left_tv 修复 Script Control result 文件清理重试后，正式 campaign 20260504_143730 已可在无 WinError 32 的情况下完整收官；当前持久化 best 已到 27.492494，关键有效链路是 explore 先把 lens_fov 推到 192.2，再在 refine 中通过 joint yaw 89.1517 -> 89.1217、pitch 24.8085 -> 24.8385、pos_x 3.1873 -> 3.1833 把总分从 28.925 压到 27.492。当前主瓶颈仍是 S3≈7.98，其次是 G1_center≈3.11 与 B2≈2.47。
- 2026-05-04 left_tv 24 分段验证：先从 25.214 basin 短跑 1x8+80，refine 中 roll 负向到 0.6328 先把 best 压到 24.8948；随后在同一套 acceptance 下再继续，lens_fov 从 191.9 微调到 192.1，又把持久化 best 压到 24.573175。当前有效 basin 为 pitch=24.8385, yaw=89.0317, lens_fov=192.1, pos_x=3.1753, pos_y=1.0222, pos_z=1.081, roll=0.6328。pitch 在该 basin 周围几乎全是 critical degrade，说明应保留当前顺序但避免额外放大 pitch 半径。
- 2026-05-04 left_tv 从 23.802949 继续下探到 23.312582，当前有效 basin 为 pitch=24.8985, yaw=88.9867, lens_fov=192.1, pos_x=3.1733, pos_y=1.0222, pos_z=1.083, roll=0.6128。决定性改进出现在 refine 的 yaw 负向一阶（iter_0139）并叠加较早的 pos_x 负向一阶与 roll 负向；lens_fov、pos_z、pitch 在这一段大多只会抬高总分或触发 S3 critical degrade，说明 23 分段仍应沿 yaw-/pos_x-/roll- 方向继续正式长跑，而不是重新放大 pitch 或 lens_fov 探索。
- 2026-05-04 left_tv 从 23.312582 继续压到 23.173202，当前有效 basin 为 pitch=24.8985, yaw=88.9717, lens_fov=192.1, pos_x=3.1733, pos_y=1.0222, pos_z=1.083, roll=0.5928。有效链路是先在 refine 中把 roll 负向到 0.5928，再由 yaw 负向一阶把 best 从 23.242448 压到 23.173202；pos_x 在这一段两侧几乎都进入 critical degrade，pos_z 基本整段失效，说明 23.17 分段更像 yaw-/roll- 的窄谷，后续继续 formal 时应优先观察这两条方向，少指望 pos_x/pos_z 再给增益。
- 2026-05-04 left_tv 从 23.173202 显著下探到 22.625368，当前有效 basin 为 pitch=24.8985, yaw=88.9567, lens_fov=192.1, pos_x=3.1693, pos_y=1.0222, pos_z=1.083, roll=0.5928。关键变化不是继续压 roll，而是 pos_x 在 explore/refine 里出现新的稳定负向窗口：3.1733 -> 3.1713 -> 3.1693 连续把 best 压到 22.809770，随后 yaw 负向一阶再把 best 压到 22.625368；roll 负向在这一段反而整体失效并频繁触发 critical degrade，说明 22 分段的主方向已从 yaw-/roll- 过渡成 yaw-/pos_x- 窄谷。
- 2026-05-04 left_tv 进入 22.6 分段后，短验证口径 `1x8+80` 失去判别力：无论是否缩细 yaw/pos_x 步长，explore 都会被 pitch 正向 2x 拉到约 24.996，再从错误 basin 进入 refine，因此这类超短 run 不再适合验证 22.6 附近的小改动；该分段更适合直接 formal 长跑，或至少避免让 explore 过早被 pitch 正向接管。
- 2026-05-04 left_tv 在 22.6 分段把 `optimization_order` 从 `pitch,yaw,lens_fov,pos_x,...` 调整为 `yaw,pos_x,pitch,lens_fov,...` 后，短验证不再一开始就被 pitch 正向 2x 拉去约 24.996。Explore 会先沿 yaw 扫描，refine 也能很快把 seed 拉回当前 best≈22.625。这个改动尚未立刻打出新低，但已证明比“pitch 先扫”的行为更干净，适合作为后续 formal 的基线。
- 2026-05-04 left_tv 在 22.6 分段把 `priority_board_acceptance.max_total_score_worsen` 与 `joint_exploration.max_single_score_worsen` 从 4.x 收紧到 1.5 后，短验证显示两个目标同时成立：一是 `yaw` 负向的 `S3` 改善仍可被接受（22.625 -> 23.698 这类约 +1.07 总分让步仍保留），二是先前会把轨迹拖去坏支线的 `pitch=24.9585`、`lens_fov=192.2+` 等偏移会被 `priority_worsen_limit_exceeded` 挡掉。结果是 refine 会回到全局 best=22.625368，而不会再把 config 初值自动写成更差的 23.44 basin。
- 2026-05-04 在收紧 acceptance 后再次测试更细的 `yaw/pos_x` 步长（yaw 0.01 / pos_x 0.001）仍然失败，但失败形态已不同于旧结论：不是被 `pitch` 24.9585 劫持，而是更细步长本身没有打开新谷底，explore best 直接停在当前 22.625368，refine 还会滑到约 23.6549，因此这条 finer-step 假设在 22.6 分段可继续视为无效，应保持 yaw 0.015、pos_x 0.002 的原步长。
- 2026-05-04 把 `priority_board_acceptance.board_ids` 从 `[S3, G1_center, S2]` 暂时收成仅 `[S3]` 后，短验证仍可稳定回到 22.625368，而且此前常见的 `G1_center`/`S2` override 绕路不再出现。该改动尚未直接打出新低，但比多目标 override 的行为更干净，值得作为下一轮 formal 的 baseline。

- 2026-05-05 left_tv 在 S3-only formal 中出现过一次中途崩溃：`movie dde_fbo capture failed: remote server cannot handle this command`，随后 `Script Control apply` 也连续报同样错误，说明问题在 DDE/TclEval 服务瞬态拒绝命令，而不是单次参数或图像路径。恢复逻辑已在 `camera_calibration.py` 中补成：遇到这类错误先做一次不允许 cache fallback 的 DDE health probe（movie size probe）等待服务恢复，再重试参数回放；不要仅在坏状态上直接重复 `_apply_value_map`。
- 2026-05-05 left_tv 最小 smoke（campaign 20260505_013220, 1x2+2）证明新的 `dde_recovery_probe` 已在真实路径触发，但当 `movie_size_probe` 连续 6 次都报 `remote server cannot handle this command` 时，4 轮 probe 仍全部失败，随后 `script_control_apply` 与 `movie_capture` 继续整段失败，最终 `multistart_summary.json` 记录 `best_run=null`、唯一 run 直接死于 `movie dde_fbo capture failed: remote server cannot handle this command`。这说明当前 blocker 已不是优化策略，而是外部 `TclEval/CarMaker` DDE 会话整体失活；在现有主线代码只支持 DDE Script Control + `dde_fbo` capture 的前提下，继续 formal 没有意义，必须先恢复或重启外部会话。
- 2026-05-05 left_tv 在整段 `remote server cannot handle this command`、`dde_recovery_probe` 也无效的状态下，单独重启 IPG-MOVIE 不足以恢复；但重启整台电脑后，最小 smoke（campaign 20260505_143627, 1x2+2）恢复正常，`movie_size_probe`、`script_control_apply`、`movie_capture` 全部在首轮成功，且 refine 初始分数到 22.625035688877105，略优于先前 22.625368。
- 2026-05-05 left_tv formal baseline（campaign 20260505_161417，4x24 + 180 refine）在电脑重启后的健康 DDE 会话下完整跑通，未再出现 runtime/DDE 故障。Explore 的 4 个起点都没有找到比当前 22.625 盆地更深的新 basin；refine 从一个较差 seed（start_score=23.698273）最终收回到既有最优 22.625035688877105，best image 落在 `refine/iter_0057_joint_yaw.png`，但 best values 仍与现有 basin 相同。这说明当前 S3-only、tight acceptance、yaw-first baseline 已基本进入平台期：formal 还能从坏 seed 拉回 best，但没有证据显示还能稳定发现更深参数盆地。

- 2026-05-08 right_rear custom_maker 的系统性下偏根因不是单个 ROI，而是 runtime 直接拿 full ROI template 做相关匹配，模板外缘的大块空白会把峰值往边缘/下方拖。当前稳态策略是：JSON 里继续保留 full template_image 做追溯；runtime 优先用 content crop 做 match_template/match_crop，匹配失败或过弱时再回退到 full template，并始终按 matched crop 还原内容几何。这样 C1/C2 的漂移能消掉，同时避免 C3 这类因 crop 过紧而掉检。
- 2026-05-08 当前工作区的 .venv（Python 3.12）可通过在 `.venv/Lib/site-packages` 增加 `.pth` 指向 `D:/IPG/carmaker/win64-14.1/Python/python3.12` 的方式直接导入 `cmapi`、`apoc`、`infofiles`；14.0.1 安装目录本身未见对应 Python 包目录，因此当前最稳妥的 CMAPI 接入源是 14.1。