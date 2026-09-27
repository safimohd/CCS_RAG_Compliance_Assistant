<#
    run_awake.ps1 - run a long command with the laptop held awake.

    Keeps Windows from sleeping ONLY while the command runs. Nothing is saved,
    nothing is changed permanently: the moment this script exits - normally, by
    Ctrl+C, or by crash - Windows goes back to your normal power settings.

    Usage, from 05_App:

        powershell -ExecutionPolicy Bypass -File ..\run_awake.ps1 "python ingest_corpus.py --rebuild"
        powershell -ExecutionPolicy Bypass -File ..\run_awake.ps1 "python run_evaluation.py"

    Two things this CANNOT do:
      - Closing the lid still sleeps the machine. That is a separate power action
        and no program can override it. Leave the lid open.
      - On battery, Windows throttles background CPU regardless. Stay plugged in.
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Command
)

$signature = @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
'@

$power = Add-Type -MemberDefinition $signature -Name Power -Namespace Win32 -PassThru

# ES_CONTINUOUS (0x80000000) | ES_SYSTEM_REQUIRED (0x00000001) = 0x80000001
#
# These MUST be written as [uint32] decimals. PowerShell parses the hex literal
# 0x80000001 as a signed Int32, which overflows to -2147483647 and fails to cast.
$KEEP_AWAKE = [uint32]2147483649   # stay awake, screen may still switch off
$RELEASE    = [uint32]2147483648   # ES_CONTINUOUS alone - back to normal

$power::SetThreadExecutionState($KEEP_AWAKE) | Out-Null
Write-Host "Sleep suppressed. Keep the lid open and stay on AC power." -ForegroundColor Green
Write-Host "Running: $Command`n" -ForegroundColor DarkGray

$started = Get-Date
try {
    Invoke-Expression $Command
}
finally {
    $power::SetThreadExecutionState($RELEASE) | Out-Null
    $elapsed = (Get-Date) - $started
    Write-Host "`nSleep suppression released. Elapsed: $($elapsed.ToString('hh\:mm\:ss'))" -ForegroundColor Green
}
