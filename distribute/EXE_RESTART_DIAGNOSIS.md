# Exe 无限重启问题诊断

## 问题描述
打包的 CameraCalibration.exe 无限重启，关掉又会启动新的。

## 可能原因

### 1. 进程监控
检查是否有其他进程在监控和重启 CameraCalibration.exe
```bash
# 检查是否有父进程在运行
powershell -Command "Get-WmiObject Win32_Process | Where-Object {$_.CommandLine -like '*CameraCalibration*'} | Select-Object Name, ProcessId, CommandLine"
```

### 2. Windows 服务或任务计划
检查是否有 Windows 服务或任务计划程序在自动启动
```powershell
Get-ScheduledTask | Where-Object {$_.TaskName -like '*Camera*'}
Get-Service | Where-Object {$_.DisplayName -like '*Camera*'}
```

### 3. 开发工具
检查是否有开发工具在自动重启：
- IDE 热重载
- 文件监控工具
- 进程监控工具

### 4. PyInstaller 配置
检查 PyInstaller 打包配置是否有问题：
- 是否使用了 --onefile 模式
- 是否有 bootloader 问题
- 是否需要添加 --console 选项进行调试

### 5. 程序自身逻辑
检查程序是否有自动重启机制：
- 错误处理中是否有重启逻辑
- 是否有 watchdog 机制
- 是否有热重载功能

## 诊断步骤

### Step 1: 运行基础测试
```bash
cd E:\Coding\VibeCoding\cm_calib\distribute
python test_exe_no_restart.py
```

### Step 2: 检查进程树
```powershell
# 启动 Process Explorer
procexp.exe

# 或者使用 PowerShell
Get-Process | Where-Object {$_.ProcessName -like '*Camera*'} | Select-Object Id, ProcessName, StartTime
```

### Step 3: 启用调试模式
在 build_exe.bat 中添加 --console 选项：
```batch
"%PYTHON%" -OO -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --console ^  ← 添加这行
    --name "CameraCalibration" ^
    ...
```

### Step 4: 检查事件查看器
查看 Windows 事件查看器中的应用程序日志：
- 打开 eventvwr.msc
- 查看 Windows 日志 → 应用程序
- 查找 CameraCalibration 相关的错误

## 解决方案

### 方案 1: 添加调试输出
在 launch_gui.py 中添加详细的调试输出：
```python
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def main():
    logger.debug("Starting CameraCalibration...")
    logger.debug(f"Executable: {sys.executable}")
    logger.debug(f"Args: {sys.argv}")
    ...
```

### 方案 2: 检查 PyInstaller bootloader
尝试使用不同的 PyInstaller 版本或者重新打包：
```bash
pip install --upgrade pyinstaller
```

### 方案 3: 使用 --console 模式
在 build_exe.bat 中添加 --console 选项，查看控制台输出：
```batch
--console ^
```

### 方案 4: 检查依赖版本
确保 PySide6 和其他依赖的版本兼容：
```bash
pip list | grep -E "PySide6|pyinstaller"
```

## 相关文件
- `E:\Coding\VibeCoding\cm_calib\src\entry\launch_gui.py` - 入口点
- `E:\Coding\VibeCoding\cm_calib\src\gui_app\app.py` - GUI 应用
- `E:\Coding\VibeCoding\cm_calib\distribute\build_exe.bat` - 打包脚本
- `E:\Coding\VibeCoding\cm_calib\distribute\CameraCalibration.spec` - PyInstaller 配置

## 注意事项
- 绿色免安装包（cm_calib_dist）是正确的
- 问题出在 PyInstaller 打包的 exe（dist_exe）
- 需要从 PyInstaller 打包配置和程序逻辑两方面排查
