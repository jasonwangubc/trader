# Install trader as Windows Task Scheduler jobs.
# Run this ONCE as Administrator from the repo root.
# Both the backend and frontend will auto-start at every Windows login.

param (
    [switch]$FrontendOnly,
    [switch]$BackendOnly
)

$repoRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
$nodeExe   = (Get-Command node -ErrorAction SilentlyContinue)?.Source
$npmExe    = (Get-Command npm  -ErrorAction SilentlyContinue)?.Source

# ── Backend task ──────────────────────────────────────────────────────────────
if (-not $FrontendOnly) {
    if (-not (Test-Path $pythonExe)) {
        Write-Error "Python venv not found. Run: cd backend && python -m venv .venv && .venv\Scripts\pip install -e '.[data]'"
        exit 1
    }

    $backendAction = New-ScheduledTaskAction `
        -Execute    $pythonExe `
        -Argument   "-m uvicorn app.main:app --host 127.0.0.1 --port 8002 --workers 1" `
        -WorkingDirectory (Join-Path $repoRoot "backend")

    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable

    Register-ScheduledTask -TaskName "TraderBackend" `
        -Action $backendAction `
        -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
        -Settings $settings -RunLevel Highest -Force | Out-Null

    Write-Host "Backend task registered." -ForegroundColor Green
}

# ── Frontend task ─────────────────────────────────────────────────────────────
if (-not $BackendOnly) {
    if (-not $npmExe) {
        Write-Warning "npm not found in PATH — skipping frontend task. Install Node.js and re-run."
    } else {
        $frontendAction = New-ScheduledTaskAction `
            -Execute    "cmd.exe" `
            -Argument   "/c npm run dev" `
            -WorkingDirectory (Join-Path $repoRoot "frontend")

        $settings2 = New-ScheduledTaskSettingsSet `
            -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -StartWhenAvailable

        Register-ScheduledTask -TaskName "TraderFrontend" `
            -Action $frontendAction `
            -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
            -Settings $settings2 -RunLevel Highest -Force | Out-Null

        Write-Host "Frontend task registered." -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Both services will start automatically at every Windows login." -ForegroundColor Cyan
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start backend now:   Start-ScheduledTask -TaskName 'TraderBackend'"
Write-Host "  Start frontend now:  Start-ScheduledTask -TaskName 'TraderFrontend'"
Write-Host "  Stop backend:        Stop-ScheduledTask  -TaskName 'TraderBackend'"
Write-Host "  Stop frontend:       Stop-ScheduledTask  -TaskName 'TraderFrontend'"
Write-Host "  Remove all:          'TraderBackend','TraderFrontend' | Unregister-ScheduledTask -Confirm:`$false"
Write-Host ""
Write-Host "Access from other devices on the same network:"
$lanIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '192.168.*' -or $_.IPAddress -like '10.*' } | Select-Object -First 1).IPAddress
if ($lanIp) {
    Write-Host "  http://${lanIp}:3000" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Add http://${lanIp}:3000 to your Clerk dashboard allowed origins."
    Write-Host "  Then set NEXTAUTH_URL=http://${lanIp}:3000 in frontend/.env.local"
} else {
    Write-Host "  Run 'ipconfig' to find your LAN IP, then use http://[LAN-IP]:3000"
}
