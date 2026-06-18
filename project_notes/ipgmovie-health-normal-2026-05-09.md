> **状态：❌ OBSOLETE** — 临时快照，价值已过期。

- 2026-05-09 当前 IPG-MOVIE 健康快照：CarMaker.win64 正常，Movie 进程有 2 个：GPUSensor - 'kel' online 与 IPGMovie - 'kel' online。
- 当前进程启动时间快照：CarMaker.win64.exe 17:44:49；Movie.exe(GPUSensor) 17:44:50；Movie.exe(IPGMovie) 17:46:14。
- 当前 send 健康口径必须同时满足：WInfoInterps "IPG-MOVIE" 成功返回 IPG-MOVIE；send IPG-MOVIE 成功；send 返回 payload 含 Tcl patchlevel 与当前 camera。
- 2026-05-09 实测健康结果：winterps_rc=0；winterps_msg=IPG-MOVIE；send_rc=0；send_msg={ok 8.6.9 camera {CAMERA_RSI-SENSOR Vhcl.Side_Right_Rear}}。
- 当前最小恢复探针文件：SimOutput/dde_recovery_probe/probe_send_ipgmovie_compare.txt。
- 后续若用户说“现在不正常了”，优先复测同一口径：1) WInfoInterps "IPG-MOVIE"；2) 最小 send IPG-MOVIE { list ok [info patchlevel] camera $Camera::v(Name) }；3) CarMaker/Movie/GPUSensor 进程是否被重建或缺失。
- 对比判据：若 WInfoInterps 仍返回 IPG-MOVIE 但 send 失败并报 remote server cannot handle this command，则问题在 IPG-MOVIE 的 Tk send 执行面，不在 Python->DDE->CarMaker 这一段。
- 当前 Tcl/Tk 基线：CarMaker 侧 cm_patchlevel=8.6.9, cm_tk_patchLevel=8.6.9, cm_windowingsystem=win32, cm_executable=D:/IPG/carmaker/win64-14.1/GUI/HIL.exe；IPG-MOVIE 侧 ipg_patchlevel=8.6.9, ipg_tk_patchLevel=8.6.9, ipg_windowingsystem=win32, ipg_executable=D:/IPG/carmaker/win64-14.1/GUI/Movie.exe, ipg_send_command_exists=1。
- 当前驱动基线：NVIDIA GeForce RTX 4060 Laptop GPU=32.0.15.8195；Intel UHD Graphics=32.0.101.7084；OrayIddDriver Device=17.50.19.949。
- 当前二进制基线：Movie.exe ProductVersion=14,1,0,0；CarMaker.win64.exe 文件版本资源为空。
- 当前 OpenGL 命令面基线：IPG-MOVIE 内 `gl` 命令存在，但 `gl version` 和 `gl getstring ...` 不可用，不能直接从 Tcl 侧读标准 vendor/renderer/version 字符串。
- 记录基线时出现过一次瞬时 ConnectTo("TclEval","CarMaker") 失败；但立刻复跑最小 send 探针恢复正常，暂记为偶发 DDE 连接抖动，不等同于 send 主链故障。
