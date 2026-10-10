# flip_status.ps1 - one-command Genesis executor status board
# Run from any PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\hp\Desktop\code\Genesis\scripts\flip_status.ps1
$ErrorActionPreference = "SilentlyContinue"
$B = "C:\Users\hp\Desktop\code\Genesis"

Write-Host "=== GENESIS FLIP EXECUTOR STATUS  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" -ForegroundColor Cyan

# 1. process liveness
$pidFile = "$B\data\flip_exec\executor.pid"
if (Test-Path $pidFile) {
    $exPid = Get-Content $pidFile -First 1
    $alive = Get-Process -Id $exPid
    if ($alive) {
        Write-Host ("executor : ALIVE   pid {0}  (started {1:HH:mm}, CPU {1:n0}s)" -f $exPid, $alive.StartTime, $alive.TotalProcessorTime.TotalSeconds) -ForegroundColor Green
    } else {
        Write-Host ("executor : DEAD    stale pid {0} - checkpoint task auto-restarts it at 02:30, or run scripts\flip_checkpoint.py now" -f $exPid) -ForegroundColor Red
    }
} else {
    Write-Host "executor : DEAD    no pidfile (checkpoint auto-restarts at 02:30)" -ForegroundColor Red
}

# 2. last heartbeat
$hb = Select-String -Path "$B\archive\flip_exec.log" -Pattern "^\[HB" | Select-Object -Last 1
if ($hb) {
    Write-Host "heartbeat: $($hb.Line)" -ForegroundColor Gray
    Write-Host "           (phase=+1 x2 means up-phase aged 2; pos=none/long/short; day_pnl is the model book)" -ForegroundColor DarkGray
}

# 2b. deafness check (post-sleep wedge 2026-09-12): a wedged session keeps
# printing heartbeats but its tick count freezes (~3/min vs healthy ~60/min)
$hbAll = Select-String -Path "$B\archive\flip_exec.log" -Pattern "^\[HB"
$lastHb = $hbAll | Select-Object -Last 1
if ($lastHb.Line -match '^\[HB\s+([\d.]+)m\] ticks=([\d,]+)') {
    $hbMins  = [double]$Matches[1]
    $hbTicks = [long]($Matches[2] -replace ',','')
    if ($hbMins -gt 15 -and ($hbTicks / $hbMins) -lt 10) {
        Write-Host ("DEAF WARN : {0:n0} ticks over {1:n0}m = {2:n1}/min (healthy ~60/min) - session wedged; checkpoint/restart rebuilds it" -f $hbTicks, $hbMins, ($hbTicks/$hbMins)) -ForegroundColor Red
    }
}

# 3. model day state
$state = Get-Content "$B\data\flip_exec\day_state.json" -Raw | ConvertFrom-Json
Write-Host ("day state: {0}  model day PnL {1}{2}  (halt entries at -9)" -f $state.date, [math]::Round($state.day_pnl, 2), " USD")

# 4. ledger sample size
$rows = Import-Csv "$B\data\flip_exec\slippage_ledger.csv"
$sig = ($rows | Where-Object { $_.mode -notlike "PROBE*" }).Count
$needs = [Math]::Max(0, 20 - $sig)
Write-Host ("ledger    : {0} rows, {1} signal sides  ({2} more to verdict sample n>=20)" -f $rows.Count, $sig, $needs)

# 5. settled verdict (if the checkpoint has settled it)
if (Test-Path "$B\data\flip_exec\verdict.json") {
    $v = Get-Content "$B\data\flip_exec\verdict.json" -Raw | ConvertFrom-Json
    $color = @{ GO = "Green"; MARGINAL = "Yellow"; "NO-GO" = "Red" }[$v.verdict]
    Write-Host ("verdict   : {0}  (settled {1}, n={2})" -f $v.verdict, $v.settled, $v.n_signal) -ForegroundColor $color
} else {
    Write-Host "verdict   : not settled yet (freezes automatically at first n>=20 run)" -ForegroundColor DarkGray
}

# 6. last checkpoint line
$lastCk = Get-Content "$B\archive\flip_checkpoint.log" -Tail 30 | Select-String "^STATUS:" | Select-Object -Last 1
if ($lastCk) { Write-Host "checkpoint: $($lastCk.Line)" -ForegroundColor Gray }

# 7. scheduled fleet
Write-Host "tasks     :"
foreach ($t in "Genesis FlipCheckpoint Daily", "Genesis FlipTick Daily", "Genesis FlipConfirm Daily") {
    $info = Get-ScheduledTask -TaskName $t
    $state = if ($info) { $info.State } else { "MISSING" }
    Write-Host ("  {0,-38} {1}" -f $t, $state)
}

# 8. latest slippage one-liner
$lastRev = Get-Content "$B\archive\flip_checkpoint.log" -Tail 60 | Select-String "EXTRA EXECUTION COST" | Select-Object -Last 1
if ($lastRev) { Write-Host "last cost : $($lastCk.Line); $($lastRev.Line.Trim())" }
