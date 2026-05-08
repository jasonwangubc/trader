# Install trader backend as a Windows Task Scheduler job.
# Run this ONCE as Administrator from the repo root.
# The task auto-starts the backend whenever you log in.

# Detect repo root dynamically (the directory this script lives in)
$repoRoot    = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe   = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
$args        = "-m uvicorn app.main:app --host 127.0.0.1 --port 8002 --workers 1"
$workDir     = Join-Path $repoRoot "backend"
$taskName    = "TraderBackend"

if (-not (Test-Path $pythonExe)) {
    Write-Error "Python venv not found at $pythonExe. Run: cd backend && python -m venv .venv && .venv\Scripts\pip install -e '.[dev]'"
    exit 1
}

$action   = New-ScheduledTaskAction -Execute $pythonExe -Argument $args -WorkingDirectory $workDir
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName   $taskName `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -RunLevel   Highest `
    -Force

Write-Host ""
Write-Host "Task '$taskName' registered successfully." -ForegroundColor Green
Write-Host "Backend will start automatically at every Windows login."
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start now:  Start-ScheduledTask -TaskName '$taskName'"
Write-Host "  Stop:       Stop-ScheduledTask  -TaskName '$taskName'"
Write-Host "  Remove:     Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
Write-Host "  Status:     Get-ScheduledTask   -TaskName '$taskName' | Select-Object State"
