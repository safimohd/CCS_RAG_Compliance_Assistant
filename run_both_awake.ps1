<#
    run_both_awake.ps1 - run K-RERA enrichment and pincode scraper in parallel.

    Opens two separate PowerShell windows (each with sleep suppressed) so both
    scrapers run at the same time. Keep the lid open and stay on AC power.

    Usage (from the repo root):

        powershell -ExecutionPolicy Bypass -File run_both_awake.ps1

    Optional flags:
        -KreraLimit  <int>   rows to enrich   (default: 1000)
        -PincodeStart <int>  start pincode     (default: 560001)
        -PincodeEnd   <int>  end pincode       (default: 591346)
        -DryRun              pass --dry-run to both scripts (for testing)
#>

param(
    [int]   $KreraLimit    = 1000,
    [int]   $PincodeStart  = 560001,
    [int]   $PincodeEnd    = 591346,
    [switch]$DryRun
)

$repoRoot  = $PSScriptRoot
$awakeScript = Join-Path $repoRoot "run_awake.ps1"
$appDir   = Join-Path $repoRoot "05_App"

# Build command strings
$kreraArgs  = "--limit $KreraLimit"
if ($DryRun) { $kreraArgs += " --dry-run" }
$kreraCmd   = "Set-Location '$appDir'; python scrape_krera.py $kreraArgs"

$pincodeArgs = "--start $PincodeStart --end $PincodeEnd"
if ($DryRun) { $pincodeArgs += " --dry-run" }
$pincodeCmd  = "Set-Location '$appDir'; python scrape_pincodes.py $pincodeArgs"

# Wrapper that also calls run_awake.ps1 inside each window
$kreraFull   = "powershell -ExecutionPolicy Bypass -File '$awakeScript' `"$kreraCmd`""
$pincodeFull = "powershell -ExecutionPolicy Bypass -File '$awakeScript' `"$pincodeCmd`""

Write-Host ""
Write-Host "Launching two parallel scrapers..." -ForegroundColor Cyan
Write-Host "  Window 1: K-RERA enrichment  (--limit $KreraLimit)" -ForegroundColor Yellow
Write-Host "  Window 2: Pincode scraper    ($PincodeStart to $PincodeEnd)" -ForegroundColor Yellow
Write-Host ""
Write-Host "Each window holds the machine awake on its own." -ForegroundColor DarkGray
Write-Host "Keep the lid open and stay on AC power." -ForegroundColor DarkGray
Write-Host ""

# Open Window 1: K-RERA
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", "& { `$host.UI.RawUI.WindowTitle = 'K-RERA Enrichment'; $kreraFull }"
)

# Small delay so the two windows don't stack exactly on top of each other
Start-Sleep -Milliseconds 800

# Open Window 2: Pincodes
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", "& { `$host.UI.RawUI.WindowTitle = 'Pincode Scraper'; $pincodeFull }"
)

Write-Host "Both windows launched. This launcher window can be closed." -ForegroundColor Green
