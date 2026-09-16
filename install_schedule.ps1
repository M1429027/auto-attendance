param(
    [string]$TaskName = "CGU_AutoAttendance_Scan",
    [string]$StartTime = "08:30",
    [string]$Days = "MON,TUE,FRI"
)

$repoDir = $PSScriptRoot
$batPath = Join-Path $repoDir "run_scan.bat"

    if (-not (Test-Path $batPath)) {
        Write-Error "Cannot find $batPath"
        exit 1
    }

try {
    $action = New-ScheduledTaskAction -Execute $batPath -WorkingDirectory $repoDir
    $dayMap = @{
        "MON" = [System.DayOfWeek]::Monday
        "TUE" = [System.DayOfWeek]::Tuesday
        "WED" = [System.DayOfWeek]::Wednesday
        "THU" = [System.DayOfWeek]::Thursday
        "FRI" = [System.DayOfWeek]::Friday
        "SAT" = [System.DayOfWeek]::Saturday
        "SUN" = [System.DayOfWeek]::Sunday
    }

    $daysList = @()
    foreach ($d in ($Days -split ",")) {
        $key = $d.Trim().ToUpperInvariant()
        if (-not $dayMap.ContainsKey($key)) {
            throw "不支援的 Days 值: $d。請用 MON,TUE,WED,THU,FRI,SAT,SUN"
        }
        $daysList += $dayMap[$key]
    }

    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $daysList -At $StartTime
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
    Write-Host "Task created: $TaskName at $StartTime -> $batPath"
} catch {
    Write-Error ("Failed to create task: " + $_.Exception.Message)
    exit 1
}
