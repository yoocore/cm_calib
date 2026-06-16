# Stress test: 30 smoke + 5 full calibration
# Usage: powershell -File stress_test.ps1

$ErrorActionPreference = "Continue"
$workdir = "C:\CM_Projects\CMO141_Calibration\Data\Script\CameraCalibration"
$summary_file = Join-Path $workdir "stress_test_summary.json"
$results = @{smoke = @(); full = @(); started = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss") }

function Kill-IPG {
    # Silently continue if no matching processes exist
    taskkill /IM HIL.exe /F /T 2>&1 | Out-Null
    taskkill /IM Movie.exe /F /T 2>&1 | Out-Null
    taskkill /IM CarMaker.win64.exe /F /T 2>&1 | Out-Null
    Start-Sleep -Seconds 5
}

function Save-Results {
    $results | ConvertTo-Json -Depth 4 | Set-Content $summary_file
}

function Invoke-Orchestrator {
    param([string]$extraArgs = "")
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    python calibration_orchestrator.py --testrun vctc_ngxpro --camera left_tv rear_tv right_rear $extraArgs.split() 2>&1 | Out-File tmp\stress_run.log
    $sw.Stop()
    $elapsed = [math]::Round($sw.Elapsed.TotalSeconds, 0)
    
    $dirs = Get-ChildItem "SimOutput\camera_orchestration" -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $dirs) { return @{status = "CRASHED"; elapsed = $elapsed} }
    $tsk = Join-Path $dirs.FullName "task_summary.json"
    if (Test-Path $tsk) {
        $j = Get-Content $tsk -Raw | ConvertFrom-Json
        $scores = @{}
        foreach ($c in $j.per_camera) { $scores[$c.camera] = $c.calibration.final_score }
        return @{status = $j.status; elapsed = $elapsed; scores = $scores; dir = $dirs.Name }
    }
    return @{status = "CRASHED"; elapsed = $elapsed}
}

Set-Location $workdir

# --- 30 Smoke Tests ---
Write-Host "=== SMOKE TEST (30 rounds) ===" -ForegroundColor Yellow
for ($i = 1; $i -le 30; $i++) {
    Write-Host "`n--- Smoke Round $i/30 $(Get-Date -Format 'HH:mm:ss') ---"
    Kill-IPG
    $r = Invoke-Orchestrator
    $results.smoke += @{round = $i; status = $r.status; elapsed = $r.elapsed; scores = $r.scores; dir = $r.dir; time = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss")}
    Save-Results
    Write-Host "  Result: $($r.status) $($r.elapsed)s"
    foreach ($k in $r.scores.Keys) { Write-Host "    $k : $($r.scores[$k])" }
}

# --- 5 Full Calibration (2 directions + --max-iters 100) ---
Write-Host "`n=== FULL CALIBRATION (5 rounds, explore-then-refine + 100 iters) ===" -ForegroundColor Cyan
for ($i = 1; $i -le 5; $i++) {
    Write-Host "`n--- Full Round $i/5 $(Get-Date -Format 'HH:mm:ss') ---"
    Kill-IPG
    $r = Invoke-Orchestrator -extraArgs "--explore-then-refine --multi-start-iters 100 --refine-iters 100"
    $results.full += @{round = $i; status = $r.status; elapsed = $r.elapsed; scores = $r.scores; dir = $r.dir; time = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss")}
    Save-Results
    Write-Host "  Result: $($r.status) $($r.elapsed)s"
    foreach ($k in $r.scores.Keys) { Write-Host "    $k : $($r.scores[$k])" }
}

$results.finished = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss")
Save-Results
Write-Host "`n=== ALL TESTS COMPLETE ===" -ForegroundColor Green
