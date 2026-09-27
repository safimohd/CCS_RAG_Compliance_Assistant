# ============================================================
# Healthcare Dataset Consolidation
# ============================================================

$RAG = "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project"

$Destination = Join-Path $RAG "Healthcare_Master"

# Create destination
if (!(Test-Path $Destination)) {
    New-Item -ItemType Directory -Path $Destination | Out-Null
}

# ------------------------------------------------------------
# Source folders
# ------------------------------------------------------------

$Sources = @(
    "$RAG\Haryana_PMJAY_Policy_Guidelines",
    "$RAG\Healthcare_Clinical_Establishments",
    "$RAG\Healthcare_RAG_Raw",
    "$RAG\Healthcare_RAG_Remaining"
)

# ------------------------------------------------------------
# Counters
# ------------------------------------------------------------

$Inventory = @()

$Copied = 0
$Duplicates = 0
$Failed = 0
$TotalFound = 0

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "        HEALTHCARE DATASET CONSOLIDATION" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------
# Scan source folders
# ------------------------------------------------------------

foreach ($Source in $Sources) {

    if (!(Test-Path $Source)) {

        Write-Host "SOURCE FOLDER NOT FOUND:" -ForegroundColor Red
        Write-Host $Source
        Write-Host ""

        continue
    }

    Write-Host "Scanning:" -ForegroundColor Yellow
    Write-Host $Source

    $Files = Get-ChildItem `
        -Path $Source `
        -Filter "*.pdf" `
        -File `
        -Recurse

    Write-Host "Found $($Files.Count) PDF(s)" -ForegroundColor Gray
    Write-Host ""

    foreach ($File in $Files) {

        $TotalFound++

        try {

            # -----------------------------------------------
            # SHA256 hash for exact duplicate detection
            # -----------------------------------------------

            $Hash = (Get-FileHash `
                -Path $File.FullName `
                -Algorithm SHA256).Hash

            # Check whether identical content already copied
            $Existing = $Inventory | Where-Object {
                $_.SHA256 -eq $Hash
            } | Select-Object -First 1

            if ($Existing) {

                Write-Host "DUPLICATE:" -ForegroundColor DarkYellow
                Write-Host "  $($File.Name)"
                Write-Host "  Same content as: $($Existing.FileName)"
                Write-Host ""

                $Duplicates++

                $Inventory += [PSCustomObject]@{
                    FileName        = $File.Name
                    SourcePath      = $File.FullName
                    DestinationPath = $Existing.DestinationPath
                    SourceFolder    = Split-Path $Source -Leaf
                    SHA256          = $Hash
                    Status          = "Duplicate"
                }

                continue
            }

            # -----------------------------------------------
            # Clean filename
            # -----------------------------------------------

            $BaseName = [System.IO.Path]::GetFileNameWithoutExtension(
                $File.Name
            )

            $SafeName = $BaseName -replace '[<>:"/\\|?*]', '_'

            $DestinationFile = Join-Path `
                $Destination `
                "$SafeName.pdf"

            # -----------------------------------------------
            # Prevent filename collisions
            # -----------------------------------------------

            $Counter = 1

            while (Test-Path $DestinationFile) {

                $DestinationFile = Join-Path `
                    $Destination `
                    "${SafeName}_$Counter.pdf"

                $Counter++
            }

            # -----------------------------------------------
            # Copy
            # -----------------------------------------------

            Copy-Item `
                -Path $File.FullName `
                -Destination $DestinationFile `
                -ErrorAction Stop

            Write-Host "COPIED:" -ForegroundColor Green
            Write-Host "  $($File.Name)"

            $Copied++

            $Inventory += [PSCustomObject]@{
                FileName        = Split-Path $DestinationFile -Leaf
                SourcePath      = $File.FullName
                DestinationPath = $DestinationFile
                SourceFolder    = Split-Path $Source -Leaf
                SHA256          = $Hash
                Status          = "Copied"
            }

        }
        catch {

            Write-Host "FAILED:" -ForegroundColor Red
            Write-Host "  $($File.FullName)"
            Write-Host "  $($_.Exception.Message)"
            Write-Host ""

            $Failed++
        }
    }
}

# ------------------------------------------------------------
# Save inventory
# ------------------------------------------------------------

$CSVPath = Join-Path `
    $Destination `
    "healthcare_file_inventory.csv"

$Inventory | Export-Csv `
    -Path $CSVPath `
    -NoTypeInformation `
    -Encoding UTF8

# ------------------------------------------------------------
# Final summary
# ------------------------------------------------------------

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "              CONSOLIDATION COMPLETE" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

Write-Host ""
Write-Host "PDFs found        : $TotalFound"
Write-Host "Copied            : $Copied" -ForegroundColor Green
Write-Host "Duplicates        : $Duplicates" -ForegroundColor Yellow
Write-Host "Failed            : $Failed" -ForegroundColor Red

Write-Host ""
Write-Host "MASTER CORPUS:"
Write-Host $Destination

Write-Host ""
Write-Host "INVENTORY:"
Write-Host $CSVPath

Write-Host ""