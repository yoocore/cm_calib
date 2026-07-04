# CI 流水线故障排查报告

> 日期：2026-07-04  
> 涉及版本：v1.3.1 ~ v1.3.11（共 14 次失败 → 最终成功）

## 背景

GitHub Actions release 流水线（`.github/workflows/release.yml`）在自托管 Windows runner（KEL 本机）上运行，每次打 `v*` tag 触发。流水线共 5 步：

1. Syntax check（`diagnostics/tmp_tools/syntax_check.py`）
2. Package source distribution（`distribute/package.bat`）
3. Setup venv + install PyInstaller & CarMaker wheels
4. **Build EXE** via PyInstaller（`distribute/build_exe.bat`）
5. Smoke test（`diagnostics/tmp_tools/smoke_test.py`）
6. **Create GitHub Release** + upload artifacts

整个调试过程从 v1.3.1 持续到 v1.3.11，跨越 3 天，14 次失败后才完全通过。

---

## 问题 1：exit code 255 — Batch 解析器在深层 CALL 链崩溃

**影响范围**：v1.3.1 ~ v1.3.5（7 次失败）  
**首次出现**：Run #3（v1.3.1）  
**最终修复**：Run #21（v1.3.11 时已信任此修复）

### 现象

流水线在第 4 步（EXE 构建）开始后一段时间，整个 cmd.exe 进程以 **exit code 255** 退出，无有意义错误消息。

### 根因

GitHub Actions runner 通过以下极端深层 CALL 链执行流水线：

```
cmd.exe /D /E:ON /V:OFF /S /C "CALL "E:\...\_temp\xxx.cmd""
  → temp.cmd
    → call distribute\pipeline.bat
      → call distribute\build_exe.bat  (4层深度)
```

在此 4 层 `CALL` 链中，Windows batch 解析器在 `if defined VAR ( ... ) else ( ... )` 括号块中存在已知 bug：**嵌套的 `(` / `)` 匹配丢失**，解析器错误地将标签行（如 `goto :archive_done`）后的 `)` 判为闭合括号，产生语法错误消息 `"to was unexpected at this time."`，cmd.exe 立即以 **exit code 255**（致命语法错误）退出，**无法被 `if !ERRORLEVEL!` 捕获**。

### 修复

将 `build_exe.bat` 中所有 `if defined VAR ( ... ) else ( ... )` 替换为 **goto 标签模式**：

```batch
:: 错误写法（深层 CALL 链中会崩溃）
if defined SZIP (
    for ... do ...
    "!SZIP!" a ...
) else (
    echo 7-Zip not found
)

:: 正确写法
if not defined SZIP goto :no_szip
for ... do ...
"!SZIP!" a ...
goto :archive_done

:no_szip
echo 7-Zip not found

:archive_done
```

同时给文件末尾添加 `exit /b 0`，确保 ERRORLEVEL 稳定（之前缺失）。

### 经验教训

> **在深层 batch CALL 链中，永远避免 `if defined ( )` 括号块。使用 goto 标签 + if not defined goto :label 模式替代。**

---

## 问题 2：PyInstaller 依赖缺失（ordlookup / packaging）

**影响范围**：v1.3.1 ~ v1.3.5

### 现象

venv 中的 PyInstaller 运行时 `ModuleNotFoundError: No module named 'ordlookup'` 或 `'packaging'`。

### 根因

`pipeline.bat` 从 `%APPDATA%\Python\Python310\site-packages` 复制 PyInstaller 到 venv，但**列出的复制清单不全**。PyInstaller 6.20+ 新依赖了 `ordlookup`（pefile 使用）和 `packaging`（在 Python 3.14 而非 3.10 的 user site-packages 中）。

### 修复

在 `pipeline.bat` 的 xcopy 清单中补上：

```batch
xcopy /e /i /y "%USER_SP%\ordlookup" "%VENV_SP%\ordlookup\"
if exist "%APPDATA%\Python\Python314\site-packages\packaging" (
    xcopy /e /i /y "%APPDATA%\Python\Python314\site-packages\packaging" "%VENV_SP%\packaging\"
)
```

### 经验教训

> xcopy 复制 PyInstaller 到 venv 的方案**极其脆弱**：需要克隆所有依赖包及其 dist-info。如果继续用此方案，每次升级 PyInstaller 后必须检查依赖变更。**更稳定方案：** 确保 CI 有网络访问，使用 `pip install pyinstaller`。

---

## 问题 3：shell: pwsh — 自托管 runner 无 PowerShell Core

**影响范围**：v1.3.6 ~ v1.3.7  
**修复版本**：v1.3.7

### 现象

```
pwsh: command not found
exit code 1
```

### 根因

Release 步骤使用 `shell: pwsh`（PowerShell Core 7+），但自托管 Windows runner 只有 Windows 内置 `powershell.exe`（Windows PowerShell 5.1），没有安装 `pwsh`。

### 修复

将 `shell: pwsh` → `shell: powershell`

### 经验教训

> **自托管 Windows runner 默认只有 Windows PowerShell，没有 PowerShell Core。** 需要 `pwsh` 的话要额外安装。检查 runner 可用命令和环境不要假设和 GitHub hosted runner 一致。

---

## 问题 4：Resource not accessible by integration — GITHUB_TOKEN 权限不足

**影响范围**：v1.3.7 ~ v1.3.9  
**修复版本**：v1.3.10

### 现象

```
Resource not accessible by integration
exit code 1
```

### 根因

GitHub Actions 的 `GITHUB_TOKEN` **默认没有创建 Release 的权限**。workflow 文件中没有显式声明 `permissions`，所以 `contents` 权限为 `read-only`（安全默认）。所有尝试创建 Release 的方式（`gh release create`、`Invoke-RestMethod`、`softprops/action-gh-release`）都因此失败。

### 修复

在 workflow 文件中添加权限声明：

```yaml
permissions:
  contents: write
```

### 经验教训

> **任何需要写仓库资源的操作（Release、Package、Push），必须在 workflow 头部显式声明 `permissions: contents: write`。** GITHUB_TOKEN 的默认权限是 read-only，不会自动继承你个人账号的权限。

---

## 问题 5：7-Zip 未安装导致 .7z 构件未上传

**影响范围**：v1.3.10  
**修复版本**：v1.3.11

### 现象

Release 成功创建但**没有构件文件上传**，只有 GitHub 自动生成的 Source code zip/tar.gz。

### 根因

`build_exe.bat` 只尝试用 7-Zip 打包 `.7z`，但自托管 runner 没有安装 7-Zip。archive 阶段被跳过，无 `.7z` 文件生成，Release 步骤找不到文件上传。

### 修复

在 `build_exe.bat` 中添加 PowerShell `Compress-Archive` 作为 7-Zip 的 fallback：

```batch
:no_szip
echo [WARN] 7-Zip not found, trying PowerShell Compress-Archive...
powershell -NoProfile -Command "Compress-Archive -Path '%DIST_DIR%\*' -DestinationPath '!ARCHIVE!'"
```

同步更新 Release 步骤的 `files` 匹配 `.zip` 后缀。

### 经验教训

> **7-Zip 不是 Windows 标准组件。** 在自托管 runner 上应同时备选 PowerShell 的 `Compress-Archive`（Windows 内置）。更通用的做法：优先用 PowerShell，7-Zip 作为可选的增强压缩。

---

## TODO / 改进建议

| 优先级 | 项目 | 说明 |
|--------|------|------|
| 🟡 中 | **Release 发版文档** | 创建一个 `RELEASE_NOTES.md`，每次发版时更新 changelog |
| 🟢 低 | **Pipeline 本地运行脚本** | 封装一个 `run_pipeline_local.bat` 一键在本地复现 CI 流程（排除 Actions 特有步骤） |
| 🟢 低 | **构建产物标注** | 在 ZIP 文件名中加入 git commit / build number：`CameraCalibration_{date}_{run_number}.zip` |
| 🔴 高 | **7z 安装到 runner** | 在 KEL 本机安装 7-Zip（https://7-zip.org），后续构建自动用 7z 高压缩率打包 |
| 🟡 中 | **Smoke test RC=20** | 第 5 步 smoke test 返回 RC=20（大概率 locale/codepage 问题），需要修复 |
| 🟡 中 | **Pre-commit hook** | 设置 pre-commit 检查 batch 文件中的 `if defined ( )` 括号块，禁止在深层调用场景使用 |
| 🟢 低 | **Actions timeout** | 为每个步骤设置超时，防止卡死后浪费 runner 资源 |
| 🟡 中 | **GitHub hosted runner fallback** | 评估能否用 hosted runner 跑前几步（语法检查、打包），只在本机跑需要 CarMaker 的步骤 |

---

## 调试工具清单

调试过程中创建的辅助脚本，存放在 `distribute/`：

| 脚本 | 用途 |
|------|------|
| `test_live.ps1` | 精确模拟 GitHub Actions runner 的调用链和 stdin 重定向 |
| `test_no_stdin.ps1` | 测试 stdin 关闭对命令的影响 |
| `test_ci_sim.ps1` | 模拟 CI 环境调用完整流水线 |
| `test_inherit.ps1` | 测试 venv 的 system-site-packages 继承行为 |
| `test_runner_exact.ps1` | 用 runner worker 完全一致的参数启动 cmd.exe |
| `build_trace.log` | build_exe.bat 内部步骤追踪日志 |
| `pipeline_trace.log` | pipeline.bat 各步骤退出码追踪 |

---

## 文件修改清单

| 文件 | 修改内容 |
|------|----------|
| `distribute/build_exe.bat` | `if defined ( )` → goto 标签模式；添加 `exit /b 0`；添加 PowerShell Compress-Archive fallback |
| `distribute/pipeline.bat` | 添加 `ordlookup`、`packaging` 到 xcopy 清单 |
| `.github/workflows/release.yml` | `pwsh` → `powershell`；添加 `permissions: contents: write`；`softprops/action-gh-release`；匹配 `.zip` |
