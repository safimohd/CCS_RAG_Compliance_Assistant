# ============================================================
# Healthcare Document Catalog Generator
# ============================================================

$RAG = "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project"

$Master = Join-Path $RAG "Healthcare_Master"
$InventoryFile = Join-Path $Master "healthcare_file_inventory.csv"
$CatalogFile = Join-Path $Master "healthcare_document_catalog.csv"

if (!(Test-Path $InventoryFile)) {
    Write-Host "Inventory file not found:" -ForegroundColor Red
    Write-Host $InventoryFile
    exit
}

$Files = Import-Csv $InventoryFile

$Catalog = @()

foreach ($File in $Files) {

    # Only classify unique/copied documents
    if ($File.Status -ne "Copied") {
        continue
    }

    $Name = $File.FileName.ToLower()

    $Category = "Other / Review"
    $Subcategory = "Unclassified"
    $Priority = "Review"

    # --------------------------------------------------------
    # PM-JAY / Ayushman Bharat
    # --------------------------------------------------------

    if (
        $Name -match "pm.?jay" -or
        $Name -match "ayushman" -or
        $Name -match "empanel" -or
        $Name -match "insurance company" -or
        $Name -match "claim" -or
        $Name -match "beneficiary" -or
        $Name -match "escrow" -or
        $Name -match "state health agency"
    ) {
        $Category = "PM-JAY / Ayushman Bharat"
        $Subcategory = "Scheme / Hospital / Insurance Operations"
        $Priority = "High"
    }

    # --------------------------------------------------------
    # Clinical Establishments
    # --------------------------------------------------------

    elseif (
        $Name -match "clinical establishment" -or
        $Name -match "clinic" -or
        $Name -match "polyclinic" -or
        $Name -match "hospital" -or
        $Name -match "mortuary" -or
        $Name -match "sample collection" -or
        $Name -match "blood centre" -or
        $Name -match "blood transfusion"
    ) {
        $Category = "Clinical Establishments"
        $Subcategory = "Registration / Standards / Facility Requirements"
        $Priority = "High"
    }

    # --------------------------------------------------------
    # NMC / Medical Regulation
    # --------------------------------------------------------

    elseif (
        $Name -match "nmc" -or
        $Name -match "medical commission" -or
        $Name -match "medical practitioner" -or
        $Name -match "licence to practice" -or
        $Name -match "recognition of medical" -or
        $Name -match "medical institutions" -or
        $Name -match "medical qualification"
    ) {
        $Category = "Medical Regulation / NMC"
        $Subcategory = "Professional / Institutional Regulation"
        $Priority = "High"
    }

    # --------------------------------------------------------
    # Medical Education
    # --------------------------------------------------------

    elseif (
        $Name -match "graduate medical education" -or
        $Name -match "post.?graduate medical" -or
        $Name -match "pgme" -or
        $Name -match "ugme" -or
        $Name -match "cbme" -or
        $Name -match "curriculum" -or
        $Name -match "faculty" -or
        $Name -match "internship" -or
        $Name -match "teacher.*eligibility"
    ) {
        $Category = "Medical Education"
        $Subcategory = "UG / PG / Faculty / Curriculum"
        $Priority = "Medium"
    }

    # --------------------------------------------------------
    # Digital Health / Privacy
    # --------------------------------------------------------

    elseif (
        $Name -match "privacy" -or
        $Name -match "personal data" -or
        $Name -match "digital.*health" -or
        $Name -match "telemedicine" -or
        $Name -match "data protection" -or
        $Name -match "dpdp"
    ) {
        $Category = "Digital Health / Data & Privacy"
        $Subcategory = "Health Data / Privacy / Digital Health"
        $Priority = "High"
    }

    # --------------------------------------------------------
    # Clinical / Treatment Guidelines
    # --------------------------------------------------------

    elseif (
        $Name -match "clinical guideline" -or
        $Name -match "treatment guideline" -or
        $Name -match "national guideline" -or
        $Name -match "diagnosis" -or
        $Name -match "management" -or
        $Name -match "disease" -or
        $Name -match "surgery" -or
        $Name -match "oncology" -or
        $Name -match "neurology" -or
        $Name -match "orthopaedic" -or
        $Name -match "ophthalmology" -or
        $Name -match "cardiovascular" -or
        $Name -match "antimicrobial" -or
        $Name -match "rabies" -or
        $Name -match "malaria" -or
        $Name -match "tuberculosis"
    ) {
        $Category = "Clinical / Treatment Guidelines"
        $Subcategory = "Clinical Practice"
        $Priority = "Medium"
    }

    # --------------------------------------------------------
    # Public Health / Operational Guidelines
    # --------------------------------------------------------

    elseif (
        $Name -match "operational guideline" -or
        $Name -match "public health" -or
        $Name -match "health management" -or
        $Name -match "healthcare"
    ) {
        $Category = "Public Health / Operations"
        $Subcategory = "Operational / Program Guidelines"
        $Priority = "Medium"
    }

    # --------------------------------------------------------
    # Policy / Governance
    # --------------------------------------------------------

    elseif (
        $Name -match "policy" -or
        $Name -match "act" -or
        $Name -match "rules" -or
        $Name -match "regulation" -or
        $Name -match "notification" -or
        $Name -match "gazette"
    ) {
        $Category = "Law / Regulation / Policy"
        $Subcategory = "Primary Regulatory Material"
        $Priority = "High"
    }

    $Catalog += [PSCustomObject]@{
        FileName = $File.FileName
        SourcePath = $File.SourcePath
        SHA256 = $File.SHA256
        Category = $Category
        Subcategory = $Subcategory
        Priority = $Priority
    }
}

# Save catalog
$Catalog | Export-Csv `
    -Path $CatalogFile `
    -NoTypeInformation `
    -Encoding UTF8

# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "       HEALTHCARE DOCUMENT CATALOG CREATED" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

Write-Host ""
Write-Host "Documents classified: $($Catalog.Count)"

Write-Host ""
Write-Host "CATEGORY SUMMARY"
Write-Host "--------------------------------------------------"

$Catalog |
    Group-Object Category |
    Sort-Object Count -Descending |
    ForEach-Object {
        Write-Host ("{0,-40} {1,5}" -f $_.Name, $_.Count)
    }

Write-Host ""
Write-Host "Catalog saved at:"
Write-Host $CatalogFile
Write-Host ""