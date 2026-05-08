# Install trader backend as a Windows Task Scheduler job.
# Run this once as Administrator.
# The task starts the backend automatically when you log in.

$taskName    = "TraderBackend"
$pythonExe   = "C:\Users\wyc_j\dev\trader\backend\.venv\Scripts\python.exe"
$args        = "-m uvicorn app.main:app --host 127.0.0.1 --port 8002 --workers 1"
$workDir     = "C:\Users\wyc_j\dev\trader\backend"
$logFile     = "C:\Users\wyc_j\dev\trader\backend-service.log"

$action  = New-ScheduledTaskAction -Execute $pythonExe -Argument $args -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -AtLogOn

# Start minimised, no window
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

Write-Host "Task '$taskName' registered. It will start the backend at every login."
Write-Host "To start now: Start-ScheduledTask -TaskName '$taskName'"
Write-Host "To stop:      Stop-ScheduledTask  -TaskName '$taskName'"
Write-Host "To remove:    Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
