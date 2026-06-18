# Cleanup SimOutput — 每2天留1组标定运行数据

$SimOutput = "C:\CM_Projects\CMO141_Calibration\SimOutput"
$dryRun = $true  # set $false to actually delete

$targets = @("right_rear", "left_tv", "rear_tv")
$total_freed = 0

foreach ($camera in $targets) {
    $camera_dir = Join-Path $SimOutput $camera
    if (-not (Test-Path $camera_dir)) {
        Write-Host "[SKIP] $camera_dir not found"
        continue
    }

    # Collect all timestamped subdirs
    $items = Get-ChildItem $camera_dir -Directory | Where-Object {
        $_.Name -match '^(rounds_|run_|_resize_)(\d{8})_'
    }
    $dirs = @()
    foreach ($d in $items) {
        $m = [regex]::Match($d.Name, '(\d{8})')
        if ($m.Success) {
            $dt = [datetime]::ParseExact($m.Groups[1].Value, 'yyyyMMdd', $null)
            $d = $d | Add-Member -MemberType NoteProperty -Name 'Date' -Value $dt -PassThru
            $dirs += $d
        }
    }
    $dirs = $dirs | Sort-Object Date

    if ($dirs.Count -eq 0) {
        Write-Host "[SKIP] $camera — no timestamped directories found"
        continue
    }

    # Group by 2-day blocks
    $groups = @{}
    $epoch = Get-Date "2026-01-01"
    foreach ($d in $dirs) {
        $days = [math]::Floor(($d.Date - $epoch).TotalDays / 2)
        $key = "block_$days"
        if (-not $groups.ContainsKey($key)) { $groups[$key] = @() }
        $groups[$key] += $d
    }

    $kept = 0; $deleted = 0; $freed = 0
    foreach ($key in ($groups.Keys | Sort-Object)) {
        $items = $groups[$key]
        $kept++  # keep the first one
        for ($i = 1; $i -lt $items.Count; $i++) {
            $d = $items[$i]
            $size = (Get-ChildItem $d.FullName -Recurse -File -ErrorAction SilentlyContinue |
                     Measure-Object -Property Length -Sum).Sum
            if ($dryRun) {
                Write-Host "[WHATIF] $camera : would delete $($d.Name) ($([math]::Round($size/1MB)) MB)"
            } else {
                Remove-Item $d.FullName -Recurse -Force -ErrorAction SilentlyContinue
                Write-Host "[DELETED] $($d.Name) ($([math]::Round($size/1MB)) MB)"
            }
            $freed += $size
            $deleted++
        }
    }

    Write-Host "[DONE] $camera : kept $kept dirs, deleted $deleted dirs, freed $([math]::Round($freed/1MB)) MB"
    $total_freed += $freed
}

Write-Host "================================="
if ($dryRun) {
    Write-Host "TOTAL would free: $([math]::Round($total_freed/1GB,1)) GB"
    Write-Host "Edit script: set `$dryRun = `$false to execute"
} else {
    Write-Host "TOTAL freed: $([math]::Round($total_freed/1GB,1)) GB"
}
