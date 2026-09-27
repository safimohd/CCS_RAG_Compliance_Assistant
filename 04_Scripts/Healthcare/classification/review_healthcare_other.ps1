$ErrorActionPreference = "Stop"

# ============================================================
# HEALTHCARE — SECOND-PASS REVIEW OF "OTHER / REVIEW" DOCS
# ============================================================

$Root = "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project"

$Catalog = Join-Path $Root "Healthcare_Master\healthcare_document_catalog.csv"
$Output  = Join-Path $Root "Healthcare_Master\healthcare_other_review.csv"

if (-not (Test-Path $Catalog)) {
    Write-Host "ERROR: Catalog not found:" -ForegroundColor Red
    Write-Host $Catalog
    exit 1
}

$Rows = Import-Csv $Catalog | Where-Object {
    $_.Category -eq "Other / Review"
}

# ------------------------------------------------------------
# Classification helper
# ------------------------------------------------------------

function Classify-Document {
    param(
        [string]$Name
    )

    $n = $Name.ToLower()

    # ---------------------------
    # 1. REGULATORY / LEGAL
    # ---------------------------

    if ($n -match "act|statute|legislation|law|rules|rule|regulation|regulations|notification|circular|gazette|ordinance|order|amendment") {
        return @{
            Bucket   = "REVIEW - POSSIBLE REGULATORY"
            Priority = "HIGH"
            Reason   = "Filename contains legal/regulatory terminology"
        }
    }

    # ---------------------------
    # 2. LICENSING / REGISTRATION
    # ---------------------------

    if ($n -match "license|licence|licensing|registration|accreditation|recognition|renewal|approval|permit") {
        return @{
            Bucket   = "REVIEW - LICENSING / REGISTRATION"
            Priority = "HIGH"
            Reason   = "Potential licensing, registration or accreditation requirement"
        }
    }

    # ---------------------------
    # 3. COMPLIANCE / STANDARDS
    # ---------------------------

    if ($n -match "compliance|compliant|standard|standards|requirement|requirements|norms|guideline|guidelines|framework|code of practice|protocol") {
        return @{
            Bucket   = "REVIEW - COMPLIANCE / STANDARDS"
            Priority = "HIGH"
            Reason   = "Potential compliance requirement or official standard"
        }
    }

    # ---------------------------
    # 4. PATIENT / HEALTHCARE RIGHTS
    # ---------------------------

    if ($n -match "patient rights|patient charter|consumer|grievance|complaint|consent|informed consent|medical negligence|rights") {
        return @{
            Bucket   = "REVIEW - PATIENT RIGHTS"
            Priority = "HIGH"
            Reason   = "Potential patient-rights or grievance requirement"
        }
    }

    # ---------------------------
    # 5. DATA / DIGITAL HEALTH
    # ---------------------------

    if ($n -match "data|privacy|personal data|digital health|digital healthcare|health id|abdm|abha|electronic health|ehr|emr|telemedicine|tele-health|cyber|cybersecurity|information security") {
        return @{
            Bucket   = "REVIEW - DIGITAL HEALTH / DATA"
            Priority = "HIGH"
            Reason   = "Potential healthcare data or digital-health compliance"
        }
    }

    # ---------------------------
    # 6. HOSPITAL / CLINICAL OPERATIONS
    # ---------------------------

    if ($n -match "hospital|healthcare facility|health facility|clinic|clinical|laboratory|diagnostic|pharmacy|blood bank|transfusion|icu|operation theatre|ot |emergency|infection control|biomedical waste") {
        return @{
            Bucket   = "REVIEW - HEALTHCARE OPERATIONS"
            Priority = "MEDIUM"
            Reason   = "Potential healthcare-facility compliance or operational requirement"
        }
    }

    # ---------------------------
    # 7. PUBLIC HEALTH
    # ---------------------------

    if ($n -match "public health|national health|disease|surveillance|immunization|vaccination|epidemic|pandemic|outbreak|health programme|health program|maternal|child health") {
        return @{
            Bucket   = "REVIEW - PUBLIC HEALTH"
            Priority = "MEDIUM"
            Reason   = "Potential public-health regulatory/programme document"
        }
    }

    # ---------------------------
    # 8. MEDICAL EDUCATION / PROFESSIONAL
    # ---------------------------

    if ($n -match "medical education|medical college|doctor|physician|internship|residency|faculty|student|neet|curriculum|professional conduct|ethics|medical practitioner") {
        return @{
            Bucket   = "REVIEW - MEDICAL PROFESSION / EDUCATION"
            Priority = "MEDIUM"
            Reason   = "Potential professional or medical-education requirement"
        }
    }

    # ---------------------------
    # 9. INSURANCE / PM-JAY
    # ---------------------------

    if ($n -match "pm-jay|pmjay|ayushman|insurance|health insurance|claim|claims|empanelment|package rate|package rates|beneficiary") {
        return @{
            Bucket   = "REVIEW - HEALTH INSURANCE / PM-JAY"
            Priority = "HIGH"
            Reason   = "Potential healthcare financing, insurance or PM-JAY requirement"
        }
    }

    # ---------------------------
    # 10. MEDICINES / DRUGS
    # ---------------------------

    if ($n -match "drug|drugs|medicine|medicines|pharmaceutical|pharma|medical device|device|vaccine|cosmetic|cdsco|dcgi|ndps|pharmacovigilance") {
        return @{
            Bucket   = "REVIEW - DRUGS / MEDICAL DEVICES"
            Priority = "HIGH"
            Reason   = "Potential medicines, drugs or medical-device regulation"
        }
    }

    # ---------------------------
    # 11. REPORT / STATISTICS
    # ---------------------------

    if ($n -match "annual report|report|statistics|statistical|survey|dashboard|newsletter|bulletin|publication|yearbook") {
        return @{
            Bucket   = "REVIEW - REPORT / INFORMATIONAL"
            Priority = "LOW"
            Reason   = "Likely informational/reporting document rather than regulation"
        }
    }

    # ---------------------------
    # 12. FAQ / PUBLIC NOTICE
    # ---------------------------

    if ($n -match "faq|frequently asked|public notice|press release|clarification|advisory|awareness|brochure|handbook") {
        return @{
            Bucket   = "REVIEW - FAQ / ADVISORY"
            Priority = "MEDIUM"
            Reason   = "May clarify implementation but binding status needs verification"
        }
    }

    # ---------------------------
    # 13. OTHER
    # ---------------------------

    return @{
        Bucket   = "MANUAL REVIEW"
        Priority = "REVIEW"
        Reason   = "No strong regulatory signal detected from filename"
    }
}

# ------------------------------------------------------------
# Process files
# ------------------------------------------------------------

$OutputRows = @()

foreach ($row in $Rows) {

    $result = Classify-Document -Name $row.FileName

    $OutputRows += [PSCustomObject]@{
        FileName              = $row.FileName
        SourceFolder          = $row.SourceFolder
        OriginalPath          = $row.OriginalPath
        SHA256                = $row.SHA256

        CurrentCategory       = $row.Category
        CurrentPriority       = $row.Priority

        SuggestedBucket       = $result.Bucket
        SuggestedPriority     = $result.Priority
        ClassificationReason  = $result.Reason

        FinalDecision         = ""
        FinalCategory         = ""
        Notes                 = ""
    }
}

# ------------------------------------------------------------
# Save review file
# ------------------------------------------------------------

$OutputRows |
    Sort-Object SuggestedPriority, SuggestedBucket, FileName |
    Export-Csv -Path $Output -NoTypeInformation -Encoding UTF8

# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "     HEALTHCARE SECOND-PASS REVIEW CREATED" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Documents reviewed: $($OutputRows.Count)"
Write-Host ""

Write-Host "SUGGESTED BUCKET SUMMARY"
Write-Host "--------------------------------------------------"

$OutputRows |
    Group-Object SuggestedBucket |
    Sort-Object Count -Descending |
    ForEach-Object {
        Write-Host ("{0,-45} {1,5}" -f $_.Name, $_.Count)
    }

Write-Host ""
Write-Host "Suggested HIGH priority: $(
    ($OutputRows | Where-Object {$_.SuggestedPriority -eq 'HIGH'}).Count
)"

Write-Host "Suggested MEDIUM priority: $(
    ($OutputRows | Where-Object {$_.SuggestedPriority -eq 'MEDIUM'}).Count
)"

Write-Host "Suggested LOW priority: $(
    ($OutputRows | Where-Object {$_.SuggestedPriority -eq 'LOW'}).Count
)"

Write-Host "Manual review: $(
    ($OutputRows | Where-Object {$_.SuggestedPriority -eq 'REVIEW'}).Count
)"

Write-Host ""
Write-Host "Review file saved at:"
Write-Host $Output
Write-Host ""