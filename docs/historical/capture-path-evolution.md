# 抓图路径从窗口截图到 DDE/FBO 的探索历程

> **⚠️ OUTDATED (2026-06-23)** — This document was written before the codebase split (12,608-line single file → 17+ modular files) and optimizer upgrade (P0-P8). Implementation details may not match the current codebase. See `codebase_split_plan.md` and `optimizer_upgrade_plan.md` for accurate current structure.

> **归档说明**：本文档记录图像抓取从"前台窗口截图"到"IPG-MOVIE DDE/FBO 离屏抓图"的完整探索历程。
> 方案本身已淘汰，但探索过程中的探针路径和经验教训对后续类似项目仍有重要参考价值。
> 
> 当前主链：IPG-MOVIE DDE/FBO 离屏抓图
> 主文档引用：`development_process.md` 第 4 节（精简版）

---

## 背景

当写参侧已经通过 DDE 摆脱前台焦点之后，新的瓶颈就非常清楚了：不是参数写不进去，而是图像获取还停留在"看桌面"的层级。

抓图链路后面不是凭空想到 DDE/FBO 的，而是通过一轮轮探针逼出来的。

---

## 第一阶段：前台窗口截图

### 实现方式

直接对 IPG-MOVIE 前台窗口进行截图。

### 优点

- 简单直接
- 实现门槛最低

### 问题

1. 只要 IPG-MOVIE 没在前台，截图结果就不可靠
2. 一旦窗口被遮挡、最小化、切走焦点，抓图就可能失真甚至失效
3. 和写参侧一样，会把整台机器拖回"不能乱碰"的状态

### 结论

当写参侧已经通过 DDE 摆脱前台焦点之后，抓图侧成为新的瓶颈。

---

## 第二阶段：DDE 下的 GL/缓冲区探针

### 探索动机

需要找到一种不依赖前台窗口显示状态的 offscreen 路径。

### 探针路径

那段时间围绕 IPG-MOVIE 做了很多 Tcl/GL 试验，核心是在回答几个问题：

1. **IPG-MOVIE 当前 Tcl 环境里到底有哪些 gl/FBO 相关命令可用**
   - 先探 GL 上下文，确认哪些 OpenGL 命令在 Tcl 环境中可访问

2. **默认渲染缓冲区能不能直接通过 readbuffer/readpixels 读出来**
   - 探针：`gl readpixels` 的实际参数形式是什么
   - 探针：读出来的内容能不能稳定写成 PNG

3. **`gl readpixels` 的实际调用方式**
   - 确认参数格式、缓冲区选择、像素读取方式

4. **`readbuffer front/back` 的行为差异**
   - 探针：默认前后缓冲区去读，是否仍然容易和窗口显示状态绑定

5. **`bindframebuffer_read` 和 FBO 状态**
   - 探针：是否存在可以不依赖前台窗口显示状态的 offscreen 路径
   - 探针：FBO 的创建、绑定、读取流程

6. **offscreen update、wrap update、focus 相关行为**
   - 验证离屏渲染是否能独立于窗口显示状态

### 探索本质

这本质上是一条"从能不能读，到读哪一层缓冲区，再到怎样把读出来的内容变成稳定 PNG"的逆向路径。

---

## 第三阶段：IPG-MOVIE DDE/FBO 离屏抓图

### 关键结论

通过探针轮得到的结论：

1. **单纯依赖默认前后缓冲区去读，仍然容易和窗口显示状态绑定**
   - `readbuffer front/back` 不够稳定

2. **真正稳定的方式，不是继续围绕前台窗口截图打补丁，而是显式创建 capture FBO**
   - FBO（Frame Buffer Object）是 OpenGL 的离屏渲染目标

3. **完整链路**
   - 先让 IPG-MOVIE 在离屏路径里完成更新
   - 再通过 `gl bindframebuffer_read` + `gl readpixels` 把图像读到 photo 对象
   - 最后写成 PNG

### 收敛路线

```
前台窗口截图 
  -> DDE 下的 GL/缓冲区探针 
    -> 确认 readpixels/bindframebuffer_read/FBO 可用 
      -> 最终落到 IPG-MOVIE DDE/FBO 离屏抓图
```

### 意义

这一步补齐之后，写参侧和抓图侧才第一次同时进入"去前台化"状态。

此前 DDE 只解决了 Script Control 的问题；直到 FBO 抓图也跑通，整套闭环才真正摆脱了对前台窗口截图的依赖。

这也意味着标定任务运行时，电脑不再只能"腾出来给脚本用"，而是可以一边跑标定，一边正常办公、写文档、查资料和协作沟通。

---

## 经验教训总结

### 1. 抓图和写参的成熟度可能不同步

写参侧通过 DDE 摆脱前台焦点后，抓图侧可能仍停留在"看桌面"的层级。两条链需要分别成熟，才能真正的"去前台化"。

### 2. 探针是逼出来的，不是凭空想到的

从 GL 上下文到 FBO，每一步都是因为上一轮的限制走不通，才逼出下一轮探索。

### 3. 离屏渲染是稳定抓图的关键

显式创建 capture FBO，而不是依赖默认前后缓冲区，是抓图稳定性的分水岭。

### 4. 逆向路径需要耐心

"从能不能读，到读哪一层缓冲区，再到怎样把读出来的内容变成稳定 PNG"是一条需要耐心的逆向路径。

### 5. "去前台化"是两条链都成熟后才成立的状态

不是 Script Control 单独带来的，而是"写参侧 DDE + 抓图侧 DDE/FBO"两条链都完成去前台化之后，才真正成立。

---

## 相关文件

- 主文档：`project_notes/development_process.md`（精简版）
- 参数写入演进：`historical/parameter-writing-evolution.md`
- 当前主链脚本：`camera_calibration.py`
- 已验证运行链基线：`project_notes/verified_prepare_runtime_baseline_2026-05-12.md`
