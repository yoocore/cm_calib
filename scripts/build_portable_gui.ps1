param(
    [string]$PythonVersion = "3.10",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$defaultOutputDir = Join-Path $repoRoot "dist\CameraCalibrationPortable"
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = $defaultOutputDir
}

$buildRoot = Join-Path $repoRoot "build\portable_gui"
$pyinstallerDist = Join-Path $buildRoot "pyinstaller-dist"
$pyinstallerWork = Join-Path $buildRoot "work"
$pyinstallerSpec = Join-Path $buildRoot "spec"

Write-Host "Building portable GUI package..."
Write-Host "Repo root: $repoRoot"
Write-Host "Output dir: $OutputDir"

try {
    & py "-$PythonVersion" -m PyInstaller --version *> $null
} catch {
    throw "PyInstaller is not installed for Python $PythonVersion. Install it first with: py -$PythonVersion -m pip install pyinstaller"
}

if (Test-Path $buildRoot) {
    Remove-Item $buildRoot -Recurse -Force
}
if (Test-Path $OutputDir) {
    Remove-Item $OutputDir -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $pyinstallerDist | Out-Null
New-Item -ItemType Directory -Force -Path $pyinstallerWork | Out-Null
New-Item -ItemType Directory -Force -Path $pyinstallerSpec | Out-Null

& py "-$PythonVersion" -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name "CameraCalibrationGUI" `
    --hidden-import "ipaddress" `
    --distpath $pyinstallerDist `
    --workpath $pyinstallerWork `
    --specpath $pyinstallerSpec `
    (Join-Path $repoRoot "launch_gui.py")

$builtPackageDir = Join-Path $pyinstallerDist "CameraCalibrationGUI"
if (-not (Test-Path $builtPackageDir)) {
    throw "PyInstaller output not found: $builtPackageDir"
}

Copy-Item $builtPackageDir $OutputDir -Recurse -Force

$runtimeRoot = Join-Path $OutputDir "Data\Script\CameraCalibration"
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

$runtimeItems = @(
    "bootstrap_template_health_check.py",
    "calibration_orchestrator.py",
    "camera_calibration.py",
    "cmapi_testrun_control.py",
    "dde_health_check.py",
    "ipgmovie_health_monitor.py",
    "launch_gui.py",
    "portable_runtime.py",
    "precheck_cli.py",
    "README.md",
    "runtime_config_bootstrap.py",
    "script_control_apply.tcl",
    "script_control_runtime.tcl",
    "send_surface_snapshot.py",
    "verify_runtime_chain_baseline.py",
    "configs",
    "gui_app",
    "project_notes"
)

foreach ($item in $runtimeItems) {
    $source = Join-Path $repoRoot $item
    if (-not (Test-Path $source)) {
        throw "Missing runtime item: $source"
    }
    Copy-Item $source (Join-Path $runtimeRoot $item) -Recurse -Force
}

Write-Host ""
Write-Host "Portable package created:"
Write-Host "  $OutputDir"
Write-Host ""
Write-Host "End-user entry point:"
Write-Host "  $(Join-Path $OutputDir 'CameraCalibrationGUI.exe')"
Write-Host ""
Write-Host "This package contains its own GUI runtime. End users should launch the EXE directly and should not need a system Python or PySide6 installation."
