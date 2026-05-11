# CameraCalibration 开发过程纪要

## 1. 背景与初始目标

这轮开发最早的目标很直接：

1. 通过 Python 调用 CarMaker 相关接口运行标定 TestRun。
2. 自动打开 IPG-MOVIE 并切到目标 camera 视角。
3. 保存仿真标定图，与真实标定图做对比。
4. 根据差异自动微调仿真摄像头安装位置，形成闭环优化。

从目标定义上看，这是一条“全自动标定链”。但实际推进后，很快发现真正的难点不在优化算法，而在运行链路是否真的存在稳定、可调用、可重复的控制面。

---

## 2. 第一阶段：先摸清官方 API 边界

最开始先查了 doc/CMAPI 下的官方能力，重点确认三件事：

1. 能不能通过 API 控制 CarMaker 运行指定 TestRun。
2. 能不能通过 API 控制 IPG-MOVIE 打开并切换到目标相机视角。
3. 能不能通过 API 保存当前 camera view 图像，并实时改动相机安装参数。

结论很明确：

1. CarMaker 和仿真控制相关 API 是存在的，启动、停止、运行 TestRun 这类能力基本具备。
2. 直接面向 IPG-MOVIE 视角切换和截图保存的高层 API 不完整，无法支撑我们想要的那种稳定闭环。
3. 相机参数层面没有拿到一个足够高层、足够直接的“实时姿态设置接口”，大部分还是参数编辑能力。

第一轮最大的坑，就是一开始默认“文档里应该能拼出完整链路”。事实证明，官方 API 只覆盖了链路的一部分，真正卡死的是 Movie 端视角与抓图。

这一步的价值很大，因为它直接避免了后面在错误方向上继续投入。我们没有继续强行围绕“不存在的 API”写胶水，而是尽早转向替代方案。

---

## 3. 第二阶段：转向 RPA，先把最小闭环跑起来

在确认 API 覆盖不够之后，路线切到了 UI 自动化兜底。用户也同步把问题范围收窄成半自动流程：

1. 手动打开 IPG-MOVIE。
2. 手动固定到目标 camera 视角。
3. 手动打开 Settings。
4. 脚本只负责改参数、截图、比对、迭代。

这一阶段在 Data/Script/RPA 下搭了第一版原型，核心是：

1. 用 pywinauto 操作 Settings 控件。
2. 用 ImageGrab 抓取窗口画面。
3. 用 ORB 和 RANSAC 做图像相似度打分。
4. 用简单坐标下降方式尝试做参数优化。

这一版的价值，不是最终效果，而是证明了一件事：

只要把视角固定住，参数调节和图像对比这条闭环本身是可以跑起来的。

但它也很快暴露出几个明显问题：

1. 方案强依赖窗口焦点、控件位置、显示状态，环境一变就容易抖。
2. 截图来自前台窗口，不够确定，容易受遮挡、缩放、标题栏等因素影响。
3. ORB 相似度更像“图像像不像”，不是“标定板几何对不对”，解释力不够强。

所以这一阶段本质上是验证闭环，不是最终工程形态。

### 3.1 参数写入控制面的三次迭代

这轮开发里，真正探索时间最长的一段，其实不是优化器，而是“到底怎么把参数稳定写进 IPG-MOVIE”。

回头看，这条链路一共经历了三代。

#### 第一代：桌面光标点击

最早的做法，是把 Settings 窗口当作普通桌面界面处理。

参数配置里直接保存 click_x、click_y 这类相对坐标，脚本运行时把它们换算成屏幕绝对坐标，再移动鼠标、点击、粘贴、回车。连 Script Control 最早也不是直接调 DDE，而是先尝试点击它的控制台输入区域，再把命令贴进去执行。

这一代方法的优点只有一个：

实现门槛最低，能最快把第一条闭环拉起来。

但它的问题也最集中：

1. 对窗口尺寸、位置、缩放、焦点极端敏感。
2. 鼠标一旦被用户碰到，或者窗口被遮挡、移动，脚本就可能写错位置。
3. 即使在同一台机器上，控件布局轻微变化也会导致 click_x、click_y 失效。

这一代最大的工程代价，其实不只是“容易失败”，而是它会持续占用鼠标、光标和前台焦点。

这意味着电脑在标定过程中几乎只能专心跑标定，稍微做一点别的办公操作，都可能打断写参链路。换句话说，当时这台机器虽然能自动跑一些步骤，但本质上还是“标定专机”，不适合和正常办公共存。

换句话说，这一代控制面是“靠桌面事件碰运气”，只能用来快速证实方向，不适合变成长期主链。

#### 第二代：从桌面点，收敛到窗口控件

在纯坐标点击太脆之后，下一步不是立刻跳到 DDE，而是先把控制精度从“屏幕像素”提升到“窗口控件”。

这一阶段的核心思路是：

1. 先用 pywinauto 枚举 Camera Settings 里的 Edit 控件。
2. 通过 field_index、auto_id、title 等 locator 去找目标输入框。
3. 如果自动定位失败，再退回 click_x、click_y 作为兜底。

这一代比第一代前进了一大步，因为它开始利用窗口内部结构，而不再完全依赖桌面坐标。这段历史痕迹在早期配置和原型代码里都很明显，例如曾经长期保留过 field_index、click_x、click_y 这类字段，后来随着主链完全切到 Script Control 才被清理掉。

但这条路也没有彻底解决问题，因为它仍然有几个先天限制：

1. field_index 依赖控件顺序，属于脆弱定位方式。
2. 不同 backend 下能拿到的控件信息并不一致，自动定位经常需要 fallback。
3. 本质上它仍然是在“从外面操作 UI”，只是从桌面坐标变成了窗口控件句柄，稳定性虽然提升，但还没有进入真正可编程的控制面。

更重要的是，这一代虽然比纯鼠标点击前进了一步，但仍然会明显占用前台焦点，很多时候还会牵动光标位置。用户在脚本运行期间依旧不太敢随意切窗口、打字或做其他操作，因为这些日常动作仍可能干扰标定过程。

#### 第三代：识别 Tk widget，再切到 Script Control

真正的转折点，是后来发现 IPG-MOVIE 这套界面背后并不是只能靠鼠标点，它实际暴露着一套可通过 Tcl/Script Control 操作的 Tk widget 树。

尝试通过标准的Tk命令去操作IPG-MOIVE的路径经过尝试无法走通。但是一次偶然的灵光一现，想起是否可以通过Script Control UI来控制IPG-MOIVE的组件，问题的关键点就变成了如何找到IPG-MOIVE的组件入口。经过不断地逆向，终于探索出了一条可行的路径。


1. 参数不再对应“屏幕上的某个点”，而是对应 `.camera.presetFrame.evptx`、`.camera.presetFrame.evpty`、`.camera.cammoddlg.fov.e` 这类具体 widget 路径。
2. 写参动作不再是鼠标点击和键盘输入，而是 Tcl 里的 delete、insert、invoke。
3. 不再只是“写进去试试看”，而是可以顺手从 read widget 里读回值做校验。

这一代的意义非常大，因为它把系统从“桌面自动化”推进到了“应用内部控件自动化”。从这里开始，脚本才真正拥有了一个能做验证、能做失败检测、能做读回确认的可靠控制面。

但这里要特别区分清楚：Script Control 本身解决的首先是“能不能稳定、可编程地操作内部 widget”，并不是立刻解决“焦点占用”。在 Script Control 阶段，命令入口本身仍然经历过点击控制台、粘贴命令、回车执行这类路径，因此写参虽然比桌面点更可控，但前台焦点问题并没有彻底消失。

标定之旅以这样的状态运行起来了。

---

## 4. 第三阶段：从通用特征匹配，切到标定板误差驱动

随着原型跑起来，问题重点开始从“能不能自动化”转成“评估函数是不是对的”。

很快就明确了一个方向：

通用特征点相似度不适合作为标定优化目标，真正应该优化的是标定板、棋盘格、地面标记这类几何结构误差。

于是文档层面先做了两件事：

1. 把设计文档从通用图像匹配，重写为“标定板方案”。
2. 再把方案拆成实现规格，明确模块边界、输入输出、状态机和结果文件结构。

这一轮沉淀下来的核心思想是：

1. 不再只看整张图的相似度，而是按板子分区打分。
2. 同时支持 checkerboard 和 custom_groundmaker 这两类目标。
3. 单板评分与总分聚合分层处理，避免某一块偶然变好就误判整体变好。
4. 引入 priority board、degrade penalty、miss rate 等约束，避免优化器“刷分”但几何关系变差。

这一步非常关键，因为它把项目从“图像自动化脚本”拉回到了“标定问题本身”。

---

## 5. 第四阶段：从 RPA 原型，收敛到正式 CameraCalibration 实现

真正的工程收敛，发生在 Data/Script/CameraCalibration 这一套实现上。


动机还是因为旧的标定流程对办公效率的影响非常大。在脚本运行期间依旧不太敢随意切窗口、打字或做其他操作，因为这些日常动作仍可能干扰标定过程。

如何把机器从“只能支持标定、基本不能被干扰”的状态，变成“标定任务持续运行，同时还能正常办公、查资料、写文档、沟通协作”的状态，是这个阶段的核心诉求。也就是说，标定不再把整台电脑锁成单任务设备，而是开始具备和日常工作并行的能力。

真正把写参链路从焦点占用里解出来的，是后面切到 Script Control DDE。DDE 让命令不必再经由 Script Control 前台控制台输入，而是可以直接把 Tcl 脚本送进去执行，这一步才真正解决了 Script Control 自身的焦点占用问题。

不过即便写参侧已经被 DDE 解开，当时抓图侧仍然可能占用前台焦点，因为截图还没有完全摆脱对 IPG-MOVIE 前台窗口链路的依赖。也就是说，那一阶段“参数写入”和“图像获取”两条链的成熟度并不同步。

这里其实还经历过一段单独的抓图侧探索，而且花的时间一点也不少。

一开始抓图靠的是前台窗口截图，优点是简单直接，缺点也同样直接：

1. 只要 IPG-MOVIE 没在前台，截图结果就不可靠。
2. 一旦窗口被遮挡、最小化、切走焦点，抓图就可能失真甚至失效。
3. 它和写参侧一样，会把整台机器拖回“不能乱碰”的状态。

所以当写参侧已经通过 DDE 摆脱前台焦点之后，新的瓶颈就非常清楚了：不是参数写不进去，而是图像获取还停留在“看桌面”的层级。

抓图链路后面不是凭空想到 DDE/FBO 的，而是通过一轮轮探针逼出来的。那段时间围绕 IPG-MOVIE 做了很多 Tcl/GL 试验，核心是在回答几个问题：

1. IPG-MOVIE 当前 Tcl 环境里到底有哪些 gl/FBO 相关命令可用。
2. 默认渲染缓冲区能不能直接通过 readbuffer/readpixels 读出来。
3. `gl readpixels` 的实际参数形式是什么，读出来的内容能不能稳定写成 PNG。
4. 是否存在可以不依赖前台窗口显示状态的 offscreen 路径。

这也是为什么后面会留下那么多 probe 痕迹：先探 GL 上下文，再探 `gl readpixels` 的调用方式，再探 `readbuffer front/back`，再探 `bindframebuffer_read` 和 FBO 状态，最后再去验证 offscreen update、wrap update、focus 相关行为。它本质上是一条“从能不能读，到读哪一层缓冲区，再到怎样把读出来的内容变成稳定 PNG”的逆向路径。

这轮探索最后得到的结论也很关键：

1. 单纯依赖默认前后缓冲区去读，仍然容易和窗口显示状态绑定在一起。
2. 真正稳定的方式，不是继续围绕前台窗口截图打补丁，而是显式创建 capture FBO。
3. 先让 IPG-MOVIE 在离屏路径里完成更新，再通过 `gl bindframebuffer_read` + `gl readpixels` 把图像读到 photo 对象，最后写成 PNG，这条链路才真正可控。

也就是说，抓图侧真正的收敛路线更准确地说是：

前台窗口截图 -> DDE 下的 GL/缓冲区探针 -> 确认 `readpixels`/`bindframebuffer_read`/FBO 可用 -> 最终落到 IPG-MOVIE DDE/FBO 离屏抓图。

这一步补齐之后，写参侧和抓图侧才第一次同时进入“去前台化”状态。此前 DDE 只解决了 Script Control 的问题；直到 FBO 抓图也跑通，整套闭环才真正摆脱了对前台窗口截图的依赖。

这一阶段做了两个决定，直接改写了主链。要强调的是，这两个决定并不是一开始就拍脑袋得出的，而是在经历了“桌面点击 -> 控件定位 -> Tk widget/Script Control”这一串试探之后，才确认下来的：

1. 参数写入不再依赖通用 UI 自动化，改为走 Script Control DDE。
2. 图像抓取不再依赖前台窗口截图，改为走 IPG-MOVIE DDE/FBO，再通过 gl readpixels 出图。

同时解决上述两个问题，才能真正摆脱前台窗口截图与焦点干扰，图像输出也从“桌面截图”变成了“渲染结果读取”。

这两个决定解决的是稳定性问题，而不是语法问题。


当前主脚本已经收敛到 [Data/Script/CameraCalibration/camera_calibration.py](Data/Script/CameraCalibration/camera_calibration.py)。

当前活动配置文件是 [Data/Script/CameraCalibration/configs/camera.rear_tv.json](Data/Script/CameraCalibration/configs/camera.rear_tv.json)。

当前活动的 Script Control 命令脚本只保留一个入口：[Data/Script/CameraCalibration/script_control_apply.tcl](Data/Script/CameraCalibration/script_control_apply.tcl)。

这一阶段的结果，是项目从“依赖桌面状态的原型”变成“以 DDE 为主控制面、以 FBO 为主抓图面”的正式链路。

---

## 6. 第五阶段：围绕主链做瘦身与工程化收口

当 DDE/FBO 主链跑通后，后面的工作重点就不再是“再多加几种备选路径”，而是持续删掉已经没有工程价值的分支，把主链压实。

这一阶段主要完成了下面几类收口工作。

### 6.1 删除历史遗留分支

逐步把 CameraCalibration 目录里不再使用的旧分支和旧文件移走，只保留当前真正使用的链路。

核心收敛点包括：

1. 不再维护多套并行抓图模式。
2. 不再维护通用窗口连接与前台截图主链。
3. Script Control 只保留一个活动脚本入口。
4. 配置文件统一放到 Data/Script/CameraCalibration/configs/ 下，按 camera.<camera>.json 命名，不再保留一堆 best、final、proposed 变体。

### 6.2 删除已经失效的兼容字段

movie_content_crop 最早是为旧抓图逻辑服务的。等主链切到 DDE/FBO 后，这个字段已经不再代表真正的运行需求，继续保留只会制造误解。

这一轮明确把它从代码、配置和文档里一起删掉，避免后面继续围绕一个历史参数做错误排查。

### 6.3 恢复直接 API 调用路径的日志能力

后来在调试时发现一个隐蔽问题：

从命令行入口跑时有 run.log，但直接从 Python API 调 optimize 时，日志初始化没有被同样走到，导致 run.log 和 continue_resume.log 消失。

这个坑的本质不是“日志没写出来”，而是“日志初始化被绑定在入口路径上了”。

后面通过补 live log 初始化逻辑，把 direct optimize 路径也统一纳入日志管理，恢复了：

1. fresh run 生成 run.log。
2. resume run 生成 continue_resume.log。

### 6.4 清理临时探针与运行垃圾

另一类工程噪声，是每组输出里都会冒出额外的 tcl 和 txt 文件。排查后确认，这些主要是 DDE 探针和抓图过程中生成的临时脚本与结果文件。

它们在失败排障时有价值，但在成功路径上持续留存，会淹没有效输出。

因此当前实现改成：

1. 成功路径自动清理临时探针与捕获脚本产物。
2. 真正长期保留的只有 run.log、result.json、截图等必要产物。

### 6.5 明确共享结果邮箱文件的定位

script_control_camera_apply_result.txt 不是历史快照，而是 Script Control 的共享返回邮箱。

这也是一处很容易误解的点。它之所以共享，是因为它承担的是“单次命令结果回传通道”角色，而不是“每轮参数版本归档”。真正需要归档的信息，应该进入隔离输出目录里的日志与结果文件。

### 6.6 补齐一个隐藏的运行前置条件

在稳定性排查中，还发现了一个非常关键但不直观的前提：

首次运行前，必须先手动打开一次 lens 页面，让 .camera.cammoddlg 这棵 widget 树在 IPG-MOVIE 中真正创建出来。

这个条件不满足时，很多 Script Control 写参失败看起来像 DDE 问题，实际是目标控件还没初始化。

这个坑后面被正式写进 [Data/Script/CameraCalibration/README.md](Data/Script/CameraCalibration/README.md)、[Data/Script/CameraCalibration/project_notes/design.md](Data/Script/CameraCalibration/project_notes/design.md) 和 [Data/Script/CameraCalibration/project_notes/spec.md](Data/Script/CameraCalibration/project_notes/spec.md)。

而且在完成一次 lens 页面初始化后，短链路 smoke 已验证：

1. Script Control 窗口可以最小化。
2. Camera Settings 可以最小化。
3. IPG-MOVIE 也可以最小化。

这说明当前主链已经不再依赖前台桌面交互，这是一个非常实质的成熟度提升。

它带来的直接收益是：标定任务运行时，电脑不再只能“腾出来给脚本用”，而是可以一边跑标定，一边正常办公、写文档、查资料和协作沟通。这个状态并不是 Script Control 单独带来的，而是“写参侧 DDE + 抓图侧 DDE/FBO”两条链都完成去前台化之后，才真正成立。对日常效率来说，这比单纯把成功率再提高一点还更重要，因为它把标定从“排他性任务”变成了“可并行任务”。

---

## 7. 第六阶段：真实优化、参数回写与本地提交

主链稳定后，工作重点开始从“结构是否合理”转到“真实运行能不能持续给出更优参数”。

这一阶段完成了三件很重要的事。

### 7.1 跑短轮，先验证优化器真的在收敛

先用短轮优化去验证当前 rear_tv 配置是否可跑、打分是否有波动、参数是否真的能朝更优方向走。

在这轮验证中，best score 已经进入 27 左右，说明主链至少具备继续优化的基础。

### 7.2 把短轮得到的更优参数回写到配置

短轮验证后，没有把结果停留在 result.json，而是把已确认更优的参数写回当前配置。

当前 [Data/Script/CameraCalibration/configs/camera.rear_tv.json](Data/Script/CameraCalibration/configs/camera.rear_tv.json) 中，pos_z.initial 已同步到 0.67，这就是一次明确的“运行结果反哺基线配置”。

### 7.3 以当前主链为界，做一次本地提交

在清理旧分支、日志和临时文件逻辑稳定后，又做了一次本地提交，作为当前阶段的收口点。

这次提交的意义不是“功能全部完成”，而是“主链已经足够清晰，可以作为后续继续跑和继续调的稳定基线”。

---

## 8. 第七阶段：长轮优化与环境问题排查

在准备启动更长时间的优化时，又踩到了一个典型环境坑：

configure_python_environment 最初指向了系统 Python，而系统 Python 里没有 cv2，结果一运行就报缺包。

这个问题最后不是通过“给系统 Python 补装一点依赖”去糊，而是明确切换到项目自己的 .venv。

这一步的经验很明确：

1. 工作区已经有独立虚拟环境时，长跑任务必须跟着工作区环境走。
2. 标定脚本依赖 OpenCV、NumPy、Pillow 这类包，解释器选错会直接让整条链路失效。

切到 .venv 后，长轮优化已经成功启动，输出目录在 SimOutput 下独立隔离。

这轮长跑最终完整跑到 iter 180 并正常退出，最终 best score 收敛到 26.148858，best values 为：

1. pos_z = 0.672
2. pitch = 17.561
3. yaw = 180.2289
4. pos_x = 0.288
5. roll = -0.02
6. pos_y = 0.041
7. lens_fov = 195.8

对应结果目录为 [SimOutput/rear_tv_20260429_154615](SimOutput/rear_tv_20260429_154615)，最终结果文件为 [SimOutput/rear_tv_20260429_154615/result.json](SimOutput/rear_tv_20260429_154615/result.json)。这说明当前链路不仅能稳定跑完，而且已经能在较长轮次下持续给出可接受的实质改进。

---

## 9. 这一路走过的坑，以及最后怎么跨过去

下面把整个过程里最关键的坑点和解决方式压缩成一个工程清单。

### 9.1 误以为官方 API 足够完整

现象：

一开始希望完全依赖 doc/CMAPI 提供的能力拼出整条自动标定链。

根因：

把 CarMaker 控制面和 IPG-MOVIE 控制面想得过于统一。

解决：

先做 API 能力盘点，确认缺口后及时切路线，不继续围绕缺失能力做无效封装。

### 9.2 修改 InfoFile 和跑 TestRun 的链路太慢，也不够实时

现象：

即使能改参数，这条链路也不适合做高频迭代优化，Movie 侧刷新和观察体验都不理想。

解决：

把主链从“改 InfoFile 再重跑”转到“实时 Script Control 写参”。

### 9.3 RPA 原型能跑，但稳定性太差

现象：

窗口状态、控件位置、前台激活、截图边界这些因素都会影响运行结果。

解决：

RPA 只用来证明闭环可行，正式实现收敛到 DDE 控制和 FBO 抓图。

### 9.4 通用特征匹配不能代表标定误差

现象：

ORB/RANSAC 能给出分数，但很难直接说明相机几何是否真的对齐。

解决：

切到多板、多 ROI、显式几何误差聚合的评估方式，用标定板问题定义标定目标。

### 9.5 抓图路径里残留了历史兼容参数

现象：

movie_content_crop 仍然存在，但当前抓图主链已经不依赖它。

解决：

从实现、配置、文档里一起删掉，避免后续误导。

### 9.6 直接调 optimize 时日志消失

现象：

CLI 有 run.log，直接 API 调用却没有。

根因：

日志初始化绑在了入口函数路径上。

解决：

把 live log 初始化下沉，让 direct optimize 也能生成 run.log 和 continue_resume.log。

### 9.7 输出目录里临时文件太多，影响阅读

现象：

每组运行都多出 tcl 和 txt，容易让人误以为它们是结果的一部分。

解决：

把它们明确界定为探针和捕获临时物，成功路径自动清理，只把必要产物留下。

### 9.8 结果邮箱文件的语义容易被误读

现象：

script_control_camera_apply_result.txt 看起来像结果归档，实际上只是共享回传通道。

解决：

在实现和说明中明确它是 mailbox，不是版本化结果文件。

### 9.9 Lens 页面初始化是隐性依赖

现象：

如果没有先打开一次 lens 页面，很多 Script Control 操作会表现得像命令失败。

解决：

把“首次手动打开 lens 页面”升格成正式前置条件，写入文档与运行说明。

### 9.10 Python 解释器选错，会把整条链路直接打断

现象：

长轮启动时报 cv2 缺失。

根因：

实际跑的是系统 Python，而不是工作区 .venv。

解决：

明确使用项目虚拟环境启动长跑任务。

---

## 10. 当前已经落地的主链

截至当前，项目已经从“探索性原型”收敛到一条可持续维护的主链：

1. 参数写入：Script Control DDE。
2. 参数读取：Script Control DDE 结果回传。
3. 图像抓取：IPG-MOVIE DDE/FBO。
4. 评分：多板联合评分，覆盖 checkerboard 与 custom_groundmaker。
5. 优化：单参数探索与 joint exploration 结合。
6. 输出：隔离 output_dir、run.log、result.json、抓图结果与最新标记文件。

当前主链对应的关键文件：

1. 主脚本：[Data/Script/CameraCalibration/camera_calibration.py](Data/Script/CameraCalibration/camera_calibration.py)
2. 当前配置：[Data/Script/CameraCalibration/configs/camera.rear_tv.json](Data/Script/CameraCalibration/configs/camera.rear_tv.json)
3. Script Control 脚本：[Data/Script/CameraCalibration/script_control_apply.tcl](Data/Script/CameraCalibration/script_control_apply.tcl)
4. 使用说明：[Data/Script/CameraCalibration/README.md](Data/Script/CameraCalibration/README.md)
5. 设计文档：[Data/Script/CameraCalibration/project_notes/design.md](Data/Script/CameraCalibration/project_notes/design.md)
6. 实现规格：[Data/Script/CameraCalibration/project_notes/spec.md](Data/Script/CameraCalibration/project_notes/spec.md)

---

## 11. 这一轮开发最值得保留的方法论

回头看，这轮工作里最有价值的，不只是留下了一版代码，而是形成了几条后面继续做类似项目时仍然适用的方法。

### 11.1 先确认控制面，再谈自动化

如果控制面不存在，后面的算法和工程封装都只是堆在沙地上。

### 11.2 先把最小闭环跑通，再决定哪里该“正式化”

RPA 原型虽然不是终点，但它帮助我们快速证明了闭环本身成立，避免一上来就过度设计。

### 11.3 一旦找到更稳定的控制面，要果断删掉旧分支

真正让项目成熟的，不是保留很多备选方案，而是及时收敛到一条稳定主链。

### 11.4 标定问题要回到几何约束，而不是停留在图像相似度

只有把评分函数和真实标定目标对齐，优化结果才有工程意义。

### 11.5 跑通之后要继续做工程治理

日志、临时文件、结果文件语义、环境一致性，这些工作看起来不像“功能开发”，但它们决定了一套系统能不能长期使用。

---

## 12. 当前阶段总结

到目前为止，这个项目已经走完了一条非常典型但也很扎实的工程路线：

1. 从“设想中的全自动 API 链路”起步。
2. 在 API 不足处退到半自动 RPA 原型。
3. 在原型阶段确认闭环可行。
4. 再把评估目标从通用图像匹配切回标定板几何误差。
5. 最终收敛到以 Script Control DDE 和 IPG-MOVIE DDE/FBO 为核心的正式实现。

这一路最大的收获，不是某一个参数最终调到了多少，而是我们已经把“哪些路不值得继续走”“哪条路是当前最稳的主链”这件事，跑清楚了。

后续继续优化 rear_tv，或者扩展到其他 camera，基本都可以在这条已经收敛的主链上继续推进，而不需要再回到早期那种高不确定性的探索状态。