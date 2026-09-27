<#
  organize_rag_project.ps1
  Indian Regulatory Compliance Intelligence Assistant - corpus organizer

  Executes _organize_manifest.csv, which lists every file individually.
  There is NO classification logic in this script: all decisions were made
  from the project's own classification CSVs and are recorded in the manifest
  and in 03_Metadata\*_corpus_mapping.csv.

  SAFETY
    - Never deletes anything.
    - Never overwrites an existing file that differs (logs CONFLICT instead).
    - Idempotent: re-running after a completed run performs no changes.
    - Writes organize_log.csv next to this script.

  USAGE
    1. Pause OneDrive syncing (tray icon -> Pause syncing -> 2 hours).
    2. cd "<project root>"
    3. powershell -ExecutionPolicy Bypass -File .\organize_rag_project.ps1
#>

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$ManifestPath = Join-Path $Root '_organize_manifest.csv'
if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Manifest not found: $ManifestPath" }
$Manifest = Import-Csv -LiteralPath $ManifestPath

Write-Host ""
Write-Host "RAG_Project organizer" -ForegroundColor Cyan
Write-Host "Root     : $Root"
Write-Host "Manifest : $($Manifest.Count) entries"
Write-Host ""

$Log  = New-Object System.Collections.Generic.List[object]
$Stat = @{ created=0; copied=0; moved=0; movedir=0; skipped=0; conflict=0; missing=0 }

function Add-Log($action,$status,$src,$dst,$note='') {
    $Log.Add([pscustomobject]@{ Action=$action; Status=$status; Source=$src; Destination=$dst; Note=$note })
}

function Ensure-Dir($path) {
    if (-not (Test-Path -LiteralPath $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        $script:Stat.created++
    }
}

function Get-Hash256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash }

# ---------------------------------------------------------------- 1. scaffold
Write-Host "[1/4] Creating folder structure..." -ForegroundColor Yellow
$Skeleton = @(
 '01_Benchmarks\Healthcare','01_Benchmarks\RERA',
 '02_Corpus\Healthcare\01_Core_Regulatory','02_Corpus\Healthcare\02_Core_PMJAY_Insurance',
 '02_Corpus\Healthcare\03_Medical_Regulation_Education','02_Corpus\Healthcare\04_Supporting_Guidance',
 '02_Corpus\Healthcare\05_Draft_Proposed','02_Corpus\Healthcare\06_Review',
 '02_Corpus\RERA\01_Central_RERA','02_Corpus\RERA\02_Karnataka_RERA',
 '02_Corpus\RERA\03_Supporting_Guidance','02_Corpus\RERA\04_Review',
 '03_Metadata\Healthcare','03_Metadata\RERA',
 '04_Scripts\Healthcare\download','04_Scripts\Healthcare\classification',
 '04_Scripts\RERA\download','04_Scripts\RERA\classification','04_Scripts\Utilities',
 '05_App',
 '06_Evaluation\Raw_Responses','06_Evaluation\Scoring','06_Evaluation\Results',
 '07_Documentation\Project_Proposal','07_Documentation\Meeting_Notes',
 '07_Documentation\Research','07_Documentation\Reports',
 '08_Archive\Raw_Downloads','08_Archive\Superseded_Collections','08_Archive\Failed_or_Unused'
)
foreach ($d in $Skeleton) { Ensure-Dir (Join-Path $Root $d) }
Write-Host "      directories created/verified: $($Skeleton.Count)"

# ------------------------------------------------------- 2. copy metadata CSVs
Write-Host "[2/4] Copying metadata CSVs (originals stay with their collection)..." -ForegroundColor Yellow
foreach ($row in ($Manifest | Where-Object Action -eq 'copy')) {
    $src = Join-Path $Root $row.Source
    $dst = Join-Path $Root $row.Destination
    if (-not (Test-Path -LiteralPath $src)) {
        if (Test-Path -LiteralPath $dst) { $Stat.skipped++; Add-Log 'copy' 'ALREADY-DONE' $row.Source $row.Destination }
        else { $Stat.missing++; Add-Log 'copy' 'SOURCE-MISSING' $row.Source $row.Destination }
        continue
    }
    Ensure-Dir (Split-Path -Parent $dst)
    if (Test-Path -LiteralPath $dst) {
        if ((Get-Hash256 $src) -eq (Get-Hash256 $dst)) { $Stat.skipped++; Add-Log 'copy' 'IDENTICAL-SKIP' $row.Source $row.Destination }
        else { $Stat.conflict++; Add-Log 'copy' 'CONFLICT-NOT-OVERWRITTEN' $row.Source $row.Destination 'destination exists with different content' }
        continue
    }
    Copy-Item -LiteralPath $src -Destination $dst
    $Stat.copied++; Add-Log 'copy' 'COPIED' $row.Source $row.Destination
}
Write-Host "      copied: $($Stat.copied)"

# --------------------------------------------------------- 3. move corpus files
Write-Host "[3/4] Moving files into the new structure..." -ForegroundColor Yellow
$n = 0
foreach ($row in ($Manifest | Where-Object Action -eq 'move')) {
    $n++
    if ($n % 50 -eq 0) { Write-Host "      ... $n" }
    $src = Join-Path $Root $row.Source
    $dst = Join-Path $Root $row.Destination
    if (-not (Test-Path -LiteralPath $src)) {
        if (Test-Path -LiteralPath $dst) { $Stat.skipped++; Add-Log 'move' 'ALREADY-DONE' $row.Source $row.Destination }
        else { $Stat.missing++; Add-Log 'move' 'SOURCE-MISSING' $row.Source $row.Destination 'neither source nor destination present' }
        continue
    }
    Ensure-Dir (Split-Path -Parent $dst)
    if (Test-Path -LiteralPath $dst) {
        if ((Get-Hash256 $src) -eq (Get-Hash256 $dst)) { $Stat.skipped++; Add-Log 'move' 'IDENTICAL-SKIP' $row.Source $row.Destination 'source left in place' }
        else { $Stat.conflict++; Add-Log 'move' 'CONFLICT-NOT-OVERWRITTEN' $row.Source $row.Destination 'destination exists with different content' }
        continue
    }
    Move-Item -LiteralPath $src -Destination $dst
    $Stat.moved++; Add-Log 'move' 'MOVED' $row.Source $row.Destination
}
Write-Host "      moved: $($Stat.moved)"

# ------------------------------------------------- 4. archive source collections
Write-Host "[4/4] Archiving superseded source collections..." -ForegroundColor Yellow
foreach ($row in ($Manifest | Where-Object Action -eq 'movedir')) {
    $src = Join-Path $Root $row.Source
    $dst = Join-Path $Root $row.Destination
    if (-not (Test-Path -LiteralPath $src)) {
        if (Test-Path -LiteralPath $dst) { $Stat.skipped++; Add-Log 'movedir' 'ALREADY-DONE' $row.Source $row.Destination }
        else { $Stat.missing++; Add-Log 'movedir' 'SOURCE-MISSING' $row.Source $row.Destination }
        continue
    }
    Ensure-Dir (Split-Path -Parent $dst)
    if (Test-Path -LiteralPath $dst) {
        # merge leftovers into the existing archive folder rather than overwrite
        Get-ChildItem -LiteralPath $src -Force | ForEach-Object {
            $t = Join-Path $dst $_.Name
            if (-not (Test-Path -LiteralPath $t)) { Move-Item -LiteralPath $_.FullName -Destination $t }
        }
        $Stat.skipped++; Add-Log 'movedir' 'MERGED-INTO-EXISTING' $row.Source $row.Destination
        continue
    }
    Move-Item -LiteralPath $src -Destination $dst
    $Stat.movedir++; Add-Log 'movedir' 'MOVED-DIR' $row.Source $row.Destination
}
Write-Host "      collections archived: $($Stat.movedir)"

# ------------------------------------------------------------------ verification
$Log | Export-Csv -LiteralPath (Join-Path $Root 'organize_log.csv') -NoTypeInformation -Encoding UTF8

$corpusPdf  = (Get-ChildItem -LiteralPath (Join-Path $Root '02_Corpus')  -Recurse -File -Filter *.pdf -ErrorAction SilentlyContinue).Count
$archivePdf = (Get-ChildItem -LiteralPath (Join-Path $Root '08_Archive') -Recurse -File -Filter *.pdf -ErrorAction SilentlyContinue).Count
$totalPdf   = (Get-ChildItem -LiteralPath $Root -Recurse -File -Filter *.pdf -ErrorAction SilentlyContinue).Count

Write-Host ""
Write-Host "================ RESULT ================" -ForegroundColor Cyan
Write-Host ("created dirs : {0}" -f $Stat.created)
Write-Host ("copied       : {0}" -f $Stat.copied)
Write-Host ("moved        : {0}" -f $Stat.moved)
Write-Host ("dirs archived: {0}" -f $Stat.movedir)
Write-Host ("skipped      : {0}" -f $Stat.skipped)
Write-Host ("CONFLICTS    : {0}" -f $Stat.conflict) -ForegroundColor $(if($Stat.conflict){'Red'}else{'Green'})
Write-Host ("MISSING      : {0}" -f $Stat.missing)  -ForegroundColor $(if($Stat.missing){'Red'}else{'Green'})
Write-Host ""
Write-Host "PDF counts"
Write-Host ("  02_Corpus  : {0}   (expected 358)" -f $corpusPdf)
Write-Host ("  08_Archive : {0}   (expected 445)" -f $archivePdf)
Write-Host ("  TOTAL      : {0}   (expected 803)" -f $totalPdf)
Write-Host ""
foreach ($b in @('Healthcare\01_Core_Regulatory','Healthcare\02_Core_PMJAY_Insurance','Healthcare\03_Medical_Regulation_Education','Healthcare\04_Supporting_Guidance','Healthcare\05_Draft_Proposed','Healthcare\06_Review','RERA\01_Central_RERA','RERA\02_Karnataka_RERA','RERA\03_Supporting_Guidance','RERA\04_Review')) {
    $p = Join-Path $Root "02_Corpus\$b"
    $c = (Get-ChildItem -LiteralPath $p -File -Filter *.pdf -ErrorAction SilentlyContinue).Count
    Write-Host ("  {0,-45} {1,4}" -f $b, $c)
}
Write-Host ""
if ($totalPdf -ne 803) { Write-Host "WARNING: total PDF count changed - inspect organize_log.csv" -ForegroundColor Red }
Write-Host "Log written to organize_log.csv" -ForegroundColor Green
Write-Host "Remember to resume OneDrive syncing." -ForegroundColor Yellow
