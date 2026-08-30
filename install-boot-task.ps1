# Register (or remove) the logon task that starts the dashboard.
#
#   powershell -ExecutionPolicy Bypass -File install-boot-task.ps1
#   powershell -ExecutionPolicy Bypass -File install-boot-task.ps1 -Remove
#
# Why this exists: the phone log holds its writes until you press send, and
# send needs the server to be up at the moment you press it. Tailscale's own
# service is Automatic and comes back after a reboot; this does not, and the
# symptom of forgetting is a send that fails from a gym, which is exactly the
# situation the queue was built for and exactly the wrong time to discover the
# laptop is idle at a login screen with nothing listening.
#
# Deliberately NOT run as SYSTEM or with a stored password. The server reaches
# into WSL, the user's venv, dashboard.db and a Tailscale identity that belongs
# to this account; running it as anyone else is a different program with the
# same name. `-LogonType Interactive` means it starts when *you* sign in and
# holds your rights and no more.

[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = "Stop"

$TaskName = "Daily Dashboard"
$root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$boot     = Join-Path $root "boot.cmd"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed the '$TaskName' logon task."
    } else {
        Write-Host "no '$TaskName' task registered; nothing to remove."
    }
    return
}

if (-not (Test-Path $boot)) { throw "boot.cmd not found beside this script: $boot" }

$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$boot`"" -WorkingDirectory $root

# A delay because logon is the busiest minute the machine has, and nothing is
# waiting on this: WSL has to start, the venv has to import FastAPI, and the
# phone cannot reach the tailnet before Tailscale is up either. Thirty seconds
# spent out of the way costs nothing and avoids racing all three.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT30S"

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

# StartWhenAvailable so a missed logon is caught up rather than skipped.
# No execution time limit: this is a server, and the default is to kill it
# after three days, which would read as the dashboard mysteriously dying on a
# long uptime. AllowStartIfOnBatteries because a laptop is the normal case here
# and "plugged in" is not.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# Hidden, because the alternative is a console window at every sign-in.
$settings.Hidden = $true

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description "Starts the dashboard server at sign-in so the phone log at /m has somewhere to send to." | Out-Null

Write-Host "registered '$TaskName': runs $boot 30s after you sign in."
Write-Host "start it now with:  Start-ScheduledTask -TaskName '$TaskName'"
