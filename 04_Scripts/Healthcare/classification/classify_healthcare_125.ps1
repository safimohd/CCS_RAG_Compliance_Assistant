$ErrorActionPreference = "Stop"

$Root = "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project"

$InputFile  = Join-Path $Root "Healthcare_Master\healthcare_other_review.csv"
$OutputFile = Join-Path $Root "Healthcare_Master\healthcare_manual_review_classified.csv"

if (-not (Test-Path $InputFile)) {
    Write-Host "ERROR: Input file not found:" -ForegroundColor Red
    Write-Host $InputFile
    exit 1
}

$Rows = Import-Csv $InputFile |
    Where-Object { $_.SuggestedPriority -eq "REVIEW" }

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "     CLASSIFYING HEALTHCARE MANUAL-REVIEW FILES" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Files to classify: $($Rows.Count)"
Write-Host ""

# ============================================================
# CLASSIFICATION FUNCTION
# ============================================================

function Classify-HealthcareFile {

    param(
        [string]$FileName
    )

    $n = $FileName.ToLower()

    # Scores
    $Regulatory = 0
    $Clinical   = 0
    $Digital    = 0
    $Insurance  = 0
    $Education  = 0
    $PublicHealth = 0
    $Informational = 0

    # -----------------------------
    # REGULATORY / LEGAL
    # -----------------------------

    if ($n -match "\bact\b|acts|law|legislation|statute") {
        $Regulatory += 8
    }

    if ($n -match "\brule\b|rules|regulation|regulations") {
        $Regulatory += 8
    }

    if ($n -match "notification|circular|gazette|ordinance|order|amendment") {
        $Regulatory += 7
    }

    if ($n -match "norms|requirement|requirements|minimum standard|minimum standards") {
        $Regulatory += 6
    }

    if ($n -match "compliance|compliant|regulatory|legal|statutory") {
        $Regulatory += 6
    }

    if ($n -match "registration|licensing|license|licence|renewal|recognition|approval|accreditation|empanelment") {
        $Regulatory += 5
    }

    # -----------------------------
    # NMC / MEDICAL PROFESSION
    # -----------------------------

    if ($n -match "nmc|national medical commission|medical council|mci") {
        $Education += 8
        $Regulatory += 5
    }

    if ($n -match "medical practitioner|professional conduct|ethics|doctor|physician") {
        $Education += 5
    }

    if ($n -match "medical college|medical education|undergraduate|postgraduate|post-graduate|internship|residency|faculty|curriculum|neet") {
        $Education += 7
    }

    # -----------------------------
    # CLINICAL ESTABLISHMENTS
    # -----------------------------

    if ($n -match "clinical establishment|clinical establishments") {
        $Regulatory += 8
        $Clinical += 6
    }

    if ($n -match "hospital|clinic|healthcare facility|health facility|nursing home") {
        $Clinical += 5
    }

    if ($n -match "laboratory|diagnostic|pathology|radiology|imaging") {
        $Clinical += 4
    }

    if ($n -match "blood bank|blood centre|transfusion|pharmacy") {
        $Clinical += 4
    }

    # -----------------------------
    # DRUGS / MEDICAL DEVICES
    # -----------------------------

    if ($n -match "cdsco|drug controller|dcgi|drugs controller") {
        $Regulatory += 7
        $Clinical += 3
    }

    if ($n -match "drug|drugs|medicine|medicines|pharmaceutical|pharma") {
        $Clinical += 4
        $Regulatory += 3
    }

    if ($n -match "medical device|medical devices|device regulation|device rules") {
        $Regulatory += 7
        $Clinical += 4
    }

    if ($n -match "vaccine|vaccination|immunization") {
        $Clinical += 3
        $PublicHealth += 4
    }

    # -----------------------------
    # DIGITAL HEALTH / DATA
    # -----------------------------

    if ($n -match "digital health|digital healthcare|abdm|abha|health id") {
        $Digital += 8
    }

    if ($n -match "telemedicine|tele-medicine|tele health|telehealth|tele-health") {
        $Digital += 7
    }

    if ($n -match "electronic health|ehr|emr|health record") {
        $Digital += 6
    }

    if ($n -match "data protection|data privacy|privacy|personal data|health data|cyber|cybersecurity|information security") {
        $Digital += 7
    }

    if ($n -match "software|digital|technology|it system|information system|interoperability") {
        $Digital += 3
    }

    # -----------------------------
    # PM-JAY / HEALTH INSURANCE
    # -----------------------------

    if ($n -match "pm-jay|pmjay|ayushman bharat|nha|national health authority") {
        $Insurance += 8
    }

    if ($n -match "health insurance|insurance|claim|claims|beneficiary|package rate|package rates") {
        $Insurance += 6
    }

    if ($n -match "empanelment|hospital empanelment|portability|preauthorization|pre-authorisation") {
        $Insurance += 5
    }

    # -----------------------------
    # PUBLIC HEALTH
    # -----------------------------

    if ($n -match "public health|national health|health programme|health program") {
        $PublicHealth += 6
    }

    if ($n -match "disease|diseases|surveillance|epidemic|pandemic|outbreak") {
        $PublicHealth += 5
    }

    if ($n -match "maternal|child health|reproductive health|nutrition|immunization|vaccination") {
        $PublicHealth += 4
    }

    if ($n -match "tb|tuberculosis|hiv|aids|malaria|dengue|fluorosis") {
        $PublicHealth += 5
    }

    # -----------------------------
    # PATIENT RIGHTS / GRIEVANCE
    # -----------------------------

    if ($n -match "patient rights|patient charter|consumer rights|rights of patient") {
        $Regulatory += 7
    }

    if ($n -match "grievance|grievance redressal|complaint|complaints|consumer") {
        $Regulatory += 6
    }

    if ($n -match "consent|informed consent|medical negligence") {
        $Regulatory += 5
    }

    # -----------------------------
    # BIO-MEDICAL WASTE / SAFETY
    # -----------------------------

    if ($n -match "biomedical waste|bio-medical waste|waste management") {
        $Regulatory += 7
        $Clinical += 4
    }

    if ($n -match "infection control|infection prevention|sterilization|safety standards") {
        $Clinical += 5
        $Regulatory += 3
    }

    # -----------------------------
    # GUIDELINES / SOP / FRAMEWORK
    # -----------------------------

    if ($n -match "guideline|guidelines|framework|protocol|sop|standard operating procedure") {
        $Clinical += 2
    }

    # -----------------------------
    # REPORT / INFORMATIONAL
    # -----------------------------

    if ($n -match "annual report|annual-report|report|statistics|statistical|survey|newsletter|bulletin|dashboard|yearbook") {
        $Informational += 7
    }

    if ($n -match "brochure|poster|leaflet|awareness|campaign|press release|press-release") {
        $Informational += 8
    }

    if ($n -match "faq|frequently asked questions") {
        $Informational += 5
    }

    # -----------------------------
    # YEAR / DRAFT SIGNALS
    # -----------------------------

    $Draft = $false

    if ($n -match "draft|proposed|consultation") {
        $Draft = $true
    }

    # ========================================================
    # DETERMINE MAIN CATEGORY
    # ========================================================

    $Scores = @{
        "Regulatory / Legal"       = $Regulatory
        "Clinical / Healthcare"    = $Clinical
        "Digital Health / Data"    = $Digital
        "PM-JAY / Insurance"       = $Insurance
        "Medical Education / NMC"  = $Education
        "Public Health"            = $PublicHealth
        "Informational"            = $Informational
    }

    $Sorted = $Scores.GetEnumerator() |
        Sort-Object Value -Descending

    $TopCategory = $Sorted[0].Key
    $TopScore    = $Sorted[0].Value

    $SecondScore = 0

    if ($Sorted.Count -gt 1) {
        $SecondScore = $Sorted[1].Value
    }

    # ========================================================
    # DECISION
    # ========================================================

    if ($TopScore -eq 0) {

        $Bucket = "MANUAL REVIEW"
        $Priority = "REVIEW"
        $Reason = "No meaningful regulatory signal detected from filename"

    }
    elseif ($Informational -ge 7 -and $TopCategory -eq "Informational") {

        $Bucket = "EXCLUDE - INFORMATIONAL"
        $Priority = "LOW"
        $Reason = "Likely report, poster, bulletin, FAQ or informational material"

    }
    elseif ($Draft -and $TopScore -lt 8) {

        $Bucket = "REVIEW - DRAFT / PROPOSED"
        $Priority = "MEDIUM"
        $Reason = "Draft/proposed document; binding status must be verified"

    }
    elseif ($TopCategory -eq "Regulatory / Legal" -and $TopScore -ge 7) {

        $Bucket = "KEEP - CORE REGULATORY"
        $Priority = "HIGH"
        $Reason = "Strong legal/regulatory signal"

    }
    elseif ($TopCategory -eq "Digital Health / Data" -and $TopScore -ge 7) {

        $Bucket = "KEEP - CORE DIGITAL HEALTH"
        $Priority = "HIGH"
        $Reason = "Strong digital-health/data compliance signal"

    }
    elseif ($TopCategory -eq "PM-JAY / Insurance" -and $TopScore -ge 7) {

        $Bucket = "KEEP - CORE PM-JAY / INSURANCE"
        $Priority = "HIGH"
        $Reason = "Strong healthcare financing/insurance compliance signal"

    }
    elseif ($TopCategory -eq "Medical Education / NMC" -and $TopScore -ge 7) {

        $Bucket = "KEEP - MEDICAL REGULATION / EDUCATION"
        $Priority = "HIGH"
        $Reason = "Strong medical-profession or education regulatory signal"

    }
    elseif ($TopScore -ge 5) {

        $Bucket = "KEEP - SUPPORTING GUIDANCE"
        $Priority = "MEDIUM"
        $Reason = "Official healthcare guidance or operational material"

    }
    else {

        $Bucket = "REVIEW - NEEDS DOCUMENT CHECK"
        $Priority = "REVIEW"
        $Reason = "Weak or ambiguous filename signal"

    }

    return @{
        Category        = $TopCategory
        Score           = $TopScore
        SecondScore     = $SecondScore
        Bucket          = $Bucket
        Priority        = $Priority
        Reason          = $Reason
        Draft           = $Draft
    }
}

# ============================================================
# PROCESS
# ============================================================

$OutputRows = @()

foreach ($row in $Rows) {

    $Result = Classify-HealthcareFile -FileName $row.FileName

    $OutputRows += [PSCustomObject]@{
        FileName              = $row.FileName
        SourceFolder          = $row.SourceFolder
        OriginalPath          = $row.OriginalPath

        SuggestedCategory     = $Result.Category
        Score                 = $Result.Score
        SecondBestScore       = $Result.SecondScore

        SuggestedBucket       = $Result.Bucket
        SuggestedPriority     = $Result.Priority

        DraftOrProposed       = $Result.Draft
        ClassificationReason  = $Result.Reason

        FinalDecision         = ""
        Notes                 = ""
    }
}

# ============================================================
# SAVE
# ============================================================

$OutputRows |
    Sort-Object SuggestedPriority, SuggestedBucket, FileName |
    Export-Csv $OutputFile -NoTypeInformation -Encoding UTF8

# ============================================================
# SUMMARY
# ============================================================

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "       CLASSIFICATION COMPLETE" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""

Write-Host "Documents classified: $($OutputRows.Count)"
Write-Host ""

Write-Host "BUCKET SUMMARY"
Write-Host "--------------------------------------------------"

$OutputRows |
    Group-Object SuggestedBucket |
    Sort-Object Count -Descending |
    ForEach-Object {
        Write-Host ("{0,-45} {1,5}" -f $_.Name, $_.Count)
    }

Write-Host ""
Write-Host "PRIORITY SUMMARY"
Write-Host "--------------------------------------------------"

$OutputRows |
    Group-Object SuggestedPriority |
    Sort-Object Name |
    ForEach-Object {
        Write-Host ("{0,-15} {1,5}" -f $_.Name, $_.Count)
    }

Write-Host ""
Write-Host "Output saved to:"
Write-Host $OutputFile
Write-Host ""
Write-Host "==================================================" -ForegroundColor Green