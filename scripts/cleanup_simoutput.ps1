# Cleanup SimOutput - keep 1 run per N days
param(
    [string]$SimOutput = "C:\CM_Projects\CMO141_Calibration\SimOutput",
    [string[]]$Targets = @("right_rear", "left_tv", "rear_tv"),
    [int]$EveryNDays = 2,
    [switch]$DryRun = $false
)

foreach ($cameraName in $Targets) {
    $cameraDir = Join-Path $SimOutput $cameraName
    if (-not (Test-Path $cameraDir)) {
        Write-Host "[SKIP] $cameraName not found"
        continue
    }

    $grouped = @{}
    Get-ChildItem $cameraDir -Directory | Where-Object { $_.Name -match '(\d{8})_\d{6}' } | ForEach-Object {
        $dateStr = $matches[1]
        $epoch = [datetime]::ParseExact($dateStr, 'yyyyMMdd', $null).ToOADate()
        $group = [math]::Floor($epoch / $EveryNDays)
        if (-not $grouped.ContainsKey($group)) {
            $grouped[$group] = @()
        }
        $grouped[$group] += $_
    }

    $keptTotal = 0
    $deletedTotal = 0
    foreach ($g in $grouped.Keys) {
        $dirs = $grouped[$g] | Sort-Object Name
        $keep = $dirs[0]
        foreach ($d in $dirs) {
            if ($d.Name -eq $keep.Name) {
                $keptTotal++
            } else {
                if ($DryRun) {
                    Write-Host "[DRY-RUN] WOULD DELETE: $($d.FullName)"
                } else {
                    Remove-Item -LiteralPath $d.FullName -Recurse -Force -ErrorAction SilentlyContinue
                    Write-Host "[DELETED] $($d.Name)"
                }
                $deletedTotal++
            }
        }
    }

    Write-Host ("--- ${cameraName}: kept=$keptTotal, deleted=$deletedTotal ---")
}
