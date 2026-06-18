> **状态：❌ OBSOLETE** — 临时快照，价值已过期。

# IPG-MOVIE Pre-Reboot Snapshot 2026-05-10

## Purpose

Record the exact pre-reboot bad state so the post-reboot session can compare what changed.

## Current conclusion

- The failure is not a full CarMaker-side DDE outage.
- CarMaker TclEval is healthy.
- CarMaker can still execute Movie-side control commands.
- The Tk send surface on the Movie side is failed for both `IPG-MOVIE` and `GPUSensor_1_0`.
- This matches the health classification `movie_commands_alive_but_tk_send_surface_failed`.

## Good baseline from 2026-05-09

- Normal snapshot reference: `project_notes/ipgmovie-health-normal-2026-05-09.md`
- Normal state had exactly one CarMaker, one GPUSensor Movie, and one GUI Movie.
- Normal send baseline:
  - `WInfoInterps "IPG-MOVIE"` returned `IPG-MOVIE`
  - `send IPG-MOVIE` succeeded
  - send payload included Tcl patchlevel and current camera

## Bad-state process stack before health probe

Snapshot command time: approximately 2026-05-10 00:41 local time.

- CarMaker PID 6660
  - command: `D:\IPG\carmaker\win64-14.1\bin\CarMaker.win64.exe`
- Movie PID 11792
  - role: GPUSensor Movie
  - command: `D:\IPG\carmaker\win64-14.1\GUI\Movie.exe -mode GPUSensor -instance 1 -CMInstance 0 -cudadevice 0 -headless -projectdir C:/CM_Projects/CMO141_Calibration -datapool D:/IPG/carmaker/win64-14.1`
- Movie PID 7948
  - role: GUI Movie
  - command: `D:\IPG\carmaker\win64-14.1\GUI\Movie.exe -CMInstance 0 -apphost localhost -apppid 6660 -projectdir C:/CM_Projects/CMO141_Calibration -datapool D:/IPG/carmaker/win64-14.1 -cmgui CarMaker`

This pre-probe stack still matched the expected 1 CarMaker + 2 Movie process layout.

## Health probe results

Probe output dir:

- `C:\CM_Projects\CMO141_Calibration\SimOutput\dde_health_check\20260510_004135`

Probe summary highlights:

- `tcleval_ping`: OK
  - detail: `ok 8.6.9`
- `interpreter_probe`: OK
  - detail: `all {IPG-MOVIE GPUSensor_1_0 CarMaker} movie IPG-MOVIE exact IPG-MOVIE`
- `movie_command_probe`: OK
  - detail: `movie_cmds Movie interps_before IPG-MOVIE start_rc 0 start_msg {} interps_after IPG-MOVIE`
- `movie_ping`: FAILED on all 3 attempts
  - detail: `remote server cannot handle this command`
- `movie_view_probe`: FAILED on all 3 attempts
  - detail: `remote server cannot handle this command`
- `gpusensor_ping`: FAILED on all 3 attempts
  - detail: `remote server cannot handle this command`

Classification:

- code: `movie_commands_alive_but_tk_send_surface_failed`
- message: `CarMaker-side Movie commands still execute, but send to both IPG-MOVIE and GPUSensor_1_0 is rejected. This isolates the fault to the Movie-side Tk send surface rather than the CarMaker Movie control API.`

Implication:

- Python -> DDE -> CarMaker is still alive.
- CarMaker-side `Movie ...` command interface is still alive.
- The failure boundary is narrower than CarMaker and broader than a single GUI Movie interpreter name.
- The broken layer is the Movie-side Tk send execution surface itself.

## Health probe side effect

`dde_health_check.py` contains this probe line in `movie_command_probe`:

- `set start_rc [catch {Movie start} start_msg]`

After running the health probe, a second GUI Movie process appeared.

Timestamped process snapshot at 2026-05-10T00:43:13+08:00:

- CarMaker PID 6660, started 2026-05-09T17:44:49+08:00
- GPUSensor Movie PID 11792, started 2026-05-09T17:44:50+08:00
- GUI Movie PID 7948, started 2026-05-09T23:29:22+08:00
- GUI Movie PID 27780, started 2026-05-10T00:41:38+08:00

Important note:

- PID 27780 was not present in the pre-probe process snapshot.
- It likely came from the `Movie start` line inside the health probe.
- Post-reboot comparisons must not treat PID 27780 as proof that the system spontaneously duplicated GUI Movie before probing.

## GUI process metrics at bad state

Sample time: 2026-05-10 around 00:48 local time.

- CarMaker PID 6660
  - SessionId: 2
  - Responding: true
  - HandleCount: 142
  - Threads: 4
  - UserObjects/GdiObjects: 0 / 0
- GUI Movie PID 7948
  - SessionId: 2
  - Responding: true
  - Main window title: `IPGMovie - 'kel' online`
  - HandleCount: 583
  - Threads: 26
  - UserObjects/GdiObjects: 95 / 129
  - WorkingSetMB: 65.8
  - PrivateMemoryMB: 3309
- GPUSensor Movie PID 11792
  - SessionId: 2
  - Responding: true
  - Main window title: `GPUSensor - 'kel' online`
  - HandleCount: 401
  - Threads: 14
  - UserObjects/GdiObjects: 45 / 87
  - WorkingSetMB: 6.3
  - PrivateMemoryMB: 3500.9
- Probe-created GUI Movie PID 27780
  - SessionId: 2
  - Responding: true
  - Main window title: `IPGMovie - 'kel' online`
  - HandleCount: 581
  - Threads: 26
  - UserObjects/GdiObjects: 94 / 129
  - WorkingSetMB: 180.2
  - PrivateMemoryMB: 3310.7

Interpretation:

- The failing state is not a simple full GUI hang.
- The GUI Movie processes are still message-pumping enough for `Responding=true` and normal-sized USER/GDI object counts.
- So the fault is narrower than “Movie window froze completely”.

## Window topology at bad state

Both GUI Movie processes and the GPUSensor Movie still owned the expected Tk/DDE-related window classes.

Observed window classes included:

- `TkTopLevel`
- `DDEMLMom`
- `DDEMLEvent`
- `TtkMonitorClass`
- `NVOpenGLPbuffer`

Observed visible top-level windows included:

- PID 7948: `IPGMovie - 'kel' online`
- PID 27780: `IPGMovie - 'kel' online`
- PID 11792: `GPUSensor - 'kel' online`

Interpretation:

- The send failure is not explained by missing Tk top-level windows.
- It is also not explained by missing DDEML registration windows.
- The registration/window objects still exist while send is already failing.

## Movie crash evidence from Windows Error Reporting

Application event logs show repeated `Movie.exe` crashes in `tk86.dll`.

Repeated crash signature:

- application: `Movie.exe`
- app version: `14.1.0.0`
- fault module: `tk86.dll`
- fault module version: `8.6.2.9`
- exception code: `0xc0000005`
- exception offset: `0x0000000000005975`

Recent WER report archives observed:

- `C:\ProgramData\Microsoft\Windows\WER\ReportArchive\AppCrash_Movie.exe_64229c3ec1a1e765c9c76370aaf83e483e102f37_f193f4e5_388cde08-d1ab-4c20-8c44-3ce0891e9e9a`
- `C:\ProgramData\Microsoft\Windows\WER\ReportArchive\AppCrash_Movie.exe_64229c3ec1a1e765c9c76370aaf83e483e102f37_f193f4e5_2f24f6fd-f765-4d5e-8a31-095da43f977e`
- `C:\ProgramData\Microsoft\Windows\WER\ReportArchive\AppCrash_Movie.exe_64229c3ec1a1e765c9c76370aaf83e483e102f37_f193f4e5_bebe270c-7cce-4a34-b57d-f76569b20d03`

The latest decoded `Report.wer` confirms:

- `EventType=APPCRASH`
- `NsAppName=Movie.exe`
- `Sig[3].Value=tk86.dll`
- `Sig[6].Value=c0000005`
- `Sig[7].Value=0000000000005975`
- loaded modules include:
  - `D:\IPG\carmaker\win64-14.1\GUI\tcl86.dll`
  - `D:\IPG\carmaker\win64-14.1\GUI\tk86.dll`
  - `D:\IPG\carmaker\win64-14.1\GUI\lib\tcldde14.dll`
  - `C:\Windows\System32\DriverStore\FileRepository\nvdm.inf_amd64_1669d27a1091c792\nvoglv64.dll`
  - Intel graphics user-mode DLLs

Interpretation:

- There is direct evidence that Movie has been crashing inside Tk, not only failing send.
- The repeated identical crash signature strongly suggests a stable product bug or a reproducible bad interaction, not random memory corruption.
- The current bad state may be a survivor state after one or more earlier Tk crashes.

## Graphics stack state

NVIDIA snapshot at 2026-05-10 00:52 local time:

- Driver Version: `581.95`
- CUDA Version: `13.0`
- GPU: `NVIDIA GeForce RTX 4060 Laptop GPU`
- Driver model: `WDDM`
- GPU recovery action: `None`
- FB memory used: `7737 MiB / 8188 MiB`
- BAR1 memory used: `8164 MiB / 8192 MiB`
- GPU utilization: `38%`
- active GPU processes included:
  - `Movie.exe` PID 7948
  - `dwm.exe` PID 11176

Video controllers present in the session:

- `NVIDIA GeForce RTX 4060 Laptop GPU` driver `32.0.15.8195`
- `Intel(R) UHD Graphics` driver `32.0.101.7084`
- `OrayIddDriver Device` driver `17.50.19.949`

Interpretation:

- This is a hybrid-graphics environment, not a single-GPU desktop-style stack.
- Movie is using a graphics stack that spans Tk, OpenGL, NVIDIA user-mode driver components, Intel graphics components, DWM, and a virtual display driver.
- Current GPU memory pressure is high enough that it should be treated as a potentially relevant condition, even though it does not prove causality by itself.

## Session anchors

- Current user SID: `S-1-5-21-2796264722-3356514500-2277750254-1001`
- Explorer session anchor:
  - PID 22588
  - SessionId 2
  - started `2026-05-09T17:42:25+08:00`
- OS boot time:
  - `2026-05-09T00:32:19+08:00`

Interpretation:

- A reboot or logout/login resets more than just CarMaker and Movie; it resets the current Windows interactive session context.
- Since the current bad state survived simple process restarts, session-level reset remains a live hypothesis.

## Additional discriminators still missing

The most valuable missing artifact is not another DDE text log. It is one of these:

- a full crash dump for `Movie.exe` when the `tk86.dll` crash happens
- a lightweight periodic health monitor that records the first instant `movie_ping` flips from ok to failed
- a synchronized snapshot taken immediately when the first send failure appears, before any recovery probe starts new windows

If a post-reboot repro is planned, these are the best next capture upgrades.

## Current-session repair attempts and outcomes

These were tried without rebooting or logging out.

1. Kill only the probe-created extra GUI Movie

- Action:
  - killed PID 27780 only
- Result:
  - no meaningful improvement
  - health classification stayed `movie_commands_alive_but_tk_send_surface_failed`
- Conclusion:
  - duplicate GUI Movie alone was not the root cause

2. Reset IME / text input session components

- Action:
  - stopped `ctfmon` and `TextInputHost`
  - restarted `ctfmon.exe`
- Result:
  - `GPUSensor_1_0` send recovered intermittently and then succeeded on the third probe attempt
  - `IPG-MOVIE` still failed
  - failure mode changed from `remote server cannot handle this command` to a narrower mixed state
- Conclusion:
  - current-session repair is possible in principle
  - session/input-framework state does influence the failure
  - but the GUI Movie target remained broken

3. Reset only GUI Movie after IME reset

- Action:
  - killed all GUI Movie processes while preserving CarMaker and GPUSensor
  - attempted CarMaker-side `Movie start`
- Result:
  - `Movie start` attempt timed out
  - `movie_ping` changed to `invalid data returned from server`
  - `gpusensor_ping` remained able to recover on later attempts
- Conclusion:
  - GUI Movie target entered a different but still bad registration/state
  - this was not a clean recovery

4. Restart Explorer shell in the same session

- Action:
  - restarted `explorer.exe` without reboot or logout
- Result:
  - `interpreter_probe` regressed to `all {GPUSensor_1_0 CarMaker} movie {} exact {}`
  - `IPG-MOVIE` registration disappeared entirely
  - `gpusensor_ping` also regressed back to failing
- Conclusion:
  - Explorer restart is not a reliable substitute for reboot/logout in this environment
  - blind session churn can make the failure mode worse rather than better

Overall conclusion from direct repair attempts:

- The current session can be perturbed, and some sub-components can temporarily recover.
- But the recovery is not stable or complete.
- The failure mode mutates across repair attempts instead of converging to healthy state.
- Based on these trials, reboot/logout remains the only known deterministic recovery, while current-session repair remains experimental and low-confidence.

## Latest calibration failure context

Failed run path:

- `C:\CM_Projects\CMO141_Calibration\SimOutput\right_rear\rounds_20260509_212544\round_09\campaign\refine\run.log`

Failure point:

- round 09 refine
- iter 39
- phase `single`
- param `pitch`
- trial `-1.0985`
- runtime error: `movie dde_fbo capture failed: remote server cannot handle this command`

Recovery behavior during failure:

- `movie_capture` retried repeatedly and failed
- `movie_size_probe` retried repeatedly and failed
- `dde_recovery_probe` exhausted all 4 attempts multiple times and failed
- final exception:
  - `RuntimeError: Failed to recover after Script Control runtime error: movie dde_fbo capture failed: remote server cannot handle this command`

Important nuance:

- The failure happened after a long period of otherwise normal DDE apply/capture activity during the same round.
- So this is not a startup-only fault.
- The send surface can degrade during a live session after many successful commands.

## What to compare after reboot or logout/login

Use the same comparison order:

1. Process stack count and startup times
   - expected healthy baseline: 1 CarMaker + 1 GPUSensor Movie + 1 GUI Movie
2. `dde_health_check.py` classification
   - bad now: `movie_commands_alive_but_tk_send_surface_failed`
3. `interpreter_probe`
   - bad now still resolves `IPG-MOVIE` and `GPUSensor_1_0`
4. `movie_ping` and `gpusensor_ping`
   - bad now both fail with `remote server cannot handle this command`
5. Real calibration smoke
   - whether repeated DDE apply + movie capture succeeds again

6. Crash and graphics correlation
  - whether new `Movie.exe -> tk86.dll -> c0000005 -> 0x5975` WER events reappear
  - whether GPU memory pressure again climbs near the same level before send fails

## Recommended pre-repro instrumentation after reboot

If configuration changes are allowed, the strongest next step is to arm one of these before reproducing again:

1. WER LocalDumps for `Movie.exe`
  - capture a dump the next time the Tk crash happens
2. ProcDump or equivalent crash monitor for `Movie.exe`
  - trigger on crash and preserve the dump outside WER temp paths
3. Low-frequency health monitor
  - poll the same `dde_health_check.py` send probes every 30 to 60 seconds
  - on first failure, immediately snapshot processes, windows, GPU usage, and health state without launching extra Movie windows

## Working hypothesis to test after reboot

- Reboot or logout/login does not merely restart CarMaker and Movie.
- It likely restores some OS session or window-system resource required by Tk send on the Movie side.
- If post-reboot the exact same process topology returns but send starts working again, the changed variable is likely session-level state rather than project config or Python logic.
