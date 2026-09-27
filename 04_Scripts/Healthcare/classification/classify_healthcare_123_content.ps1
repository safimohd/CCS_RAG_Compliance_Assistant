$ErrorActionPreference = "Continue"

$Root = "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project"

$MasterFolder = Join-Path $Root "Healthcare_Master"
$InputFile    = Join-Path $MasterFolder "healthcare_manual_review_classified.csv"
$OutputFile   = Join-Path $MasterFolder "healthcare_content_classification.csv"

# ------------------------------------------------------------
# Check pdftotext
# ------------------------------------------------------------

$PdfToText = Get-Command pdftotext -ErrorAction SilentlyContinue

if (-not $PdfToText) {
    Write-Host ""
    Write-Host "ERROR: pdftotext is not installed or not available in PATH." -ForegroundColor Red
    Write-Host ""
    Write-Host "Install it once using:"
    Write-Host "winget install oschwartz10612.Poppler"
    Write-Host ""
    Write-Host "Then CLOSE and REOPEN PowerShell and run this script again."
    exit 1
}

if (-not (Test-Path $InputFile)) {
    Write-Host "ERROR: Input CSV not found:" -ForegroundColor Red
    Write-Host $InputFile
    exit 1
}

$Rows = Import-Csv $InputFile |
    Where-Object { $_.SuggestedBucket -eq "MANUAL REVIEW" }

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "       HEALTHCARE PDF CONTENT CLASSIFICATION" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "PDFs to inspect: $($Rows.Count)"
Write-Host ""

# ------------------------------------------------------------
# Classification function
# ------------------------------------------------------------

function Classify-Text {

    param(
        [string]$Text
    )

    $t = $Text.ToLower()

    $Regulatory = 0
    $Clinical = 0
    $Digital = 0
    $Insurance = 0
    $Education = 0
    $PublicHealth = 0
    $Informational = 0

    # Authorities
    if ($t -match "ministry of health|mohfw|government of india") {
        $Regulatory += 3
    }

    if ($t -match "national medical commission|nmc|medical council of india|mci") {
        $Regulatory += 5
        $Education += 4
    }

    if ($t -match "national health authority|nha|ayushman bharat|pm-jay|pmjay") {
        $Insurance += 7
    }

    if ($t -match "cdsco|central drugs standard control|drug controller general") {
        $Regulatory += 7
        $Clinical += 3
    }

    # Legal instruments
    if ($t -match "\bact\b|acts|hereby|thereunder|statutory|statute") {
        $Regulatory += 7
    }

    if ($t -match "\brules?\b|regulation|regulations|regulatory") {
        $Regulatory += 7
    }

    if ($t -match "notification|circular|gazette|government order|office memorandum") {
        $Regulatory += 7
    }

    if ($t -match "shall|shall be|must|mandatory|prohibited|penalty|offence|punishable") {
        $Regulatory += 3
    }

    # Licensing / registration
    if ($t -match "registration|registered|licence|license|licensing|renewal|recognition|approval|accreditation|empanelment") {
        $Regulatory += 5
    }

    # Standards / compliance
    if ($t -match "minimum standards|minimum requirements|standards|compliance|compliant|requirements|norms") {
        $Regulatory += 5
    }

    # Clinical establishments
    if ($t -match "clinical establishment|hospital|clinic|nursing home|healthcare facility|health facility") {
        $Clinical += 5
    }

    if ($t -match "laboratory|diagnostic|pathology|radiology|blood bank|pharmacy|medical facility") {
        $Clinical += 4
    }

    # Medical education
    if ($t -match "medical education|medical college|undergraduate|postgraduate|post-graduate|internship|residency|faculty|curriculum|neet") {
        $Education += 7
    }

    if ($t -match "professional conduct|ethics|medical practitioner|registered medical practitioner") {
        $Education += 6
        $Regulatory += 4
    }

    # Digital health / privacy
    if ($t -match "digital health|digital healthcare|abdm|abha|health id|electronic health record|ehr|emr") {
        $Digital += 7
    }

    if ($t -match "telemedicine|telemedicine practice|tele-health|telehealth") {
        $Digital += 7
    }

    if ($t -match "personal data|health data|data protection|privacy|cybersecurity|information security") {
        $Digital += 7
        $Regulatory += 3
    }

    # Insurance
    if ($t -match "health insurance|insurance claim|claims|beneficiary|package rate|package rates|pre-authorisation|preauthorization") {
        $Insurance += 6
    }

    if ($t -match "empanelled hospital|empanelment|portability") {
        $Insurance += 5
    }

    # Public health
    if ($t -match "public health|national health programme|national health program|health programme|health program") {
        $PublicHealth += 6
    }

    if ($t -match "surveillance|epidemic|pandemic|outbreak|disease control|immunization|vaccination") {
        $PublicHealth += 5
    }

    # Patient rights
    if ($t -match "patient rights|patient charter|grievance|grievance redressal|complaint|informed consent|consumer rights") {
        $Regulatory += 6
    }

    # Clinical guidance
    if ($t -match "clinical guideline|clinical guidelines|clinical management|treatment guideline|management guideline|clinical protocol|standard treatment") {
        $Clinical += 5
    }

    # Biomedical waste / safety
    if ($t -match "biomedical waste|bio-medical waste|infection control|infection prevention|sterilization") {
        $Clinical += 5
        $Regulatory += 4
    }

    # Informational documents
    if ($t -match "annual report|annual-report|newsletter|bulletin|statistics|statistical report|survey report|dashboard") {
        $Informational += 7
    }

    if ($t -match "press release|public notice|poster|brochure|leaflet|awareness material") {
        $Informational += 6
    }

    # Draft
    $Draft = $false

    if ($t -match "\bdraft\b|proposed|consultation paper") {
        $Draft = $true
    }

    # --------------------------------------------------------
    # Determine category
    # --------------------------------------------------------

    $Scores = @{
        "Regulatory / Legal"      = $Regulatory
        "Clinical / Healthcare"   = $Clinical
        "Digital Health / Data"   = $Digital
        "PM-JAY / Insurance"      = $Insurance
        "Medical Education / NMC" = $Education
        "Public Health"           = $PublicHealth
        "Informational"           = $Informational
    }

    $Sorted = $Scores.GetEnumerator() |
        Sort-Object Value -Descending

    $TopCategory = $Sorted[0].Key
    $TopScore = $Sorted[0].Value

    if ($TopScore -eq 0) {
        return @{
            Category = "Unknown"
            Bucket = "MANUAL REVIEW"
            Priority = "REVIEW"
            Reason = "No strong content signal detected"
            Draft = $Draft
        }
    }

    if ($Draft -and $TopCategory -eq "Regulatory / Legal") {
        return @{
            Category = $TopCategory
            Bucket = "REVIEW - DRAFT / PROPOSED"
            Priority = "MEDIUM"
            Reason = "Contains draft/proposed language"
            Draft = $Draft
        }
    }

    if ($Informational -ge 7 -and $TopCategory -eq "Informational") {
        return @{
            Category = $TopCategory
            Bucket = "EXCLUDE - INFORMATIONAL"
            Priority = "LOW"
            Reason = "Appears to be a report/statistical/informational document"
            Draft = $Draft
        }
    }

    if ($TopCategory -eq "Regulatory / Legal" -and $TopScore -ge 7) {
        return @{
            Category = $TopCategory
            Bucket = "KEEP - CORE REGULATORY"
            Priority = "HIGH"
            Reason = "Strong legal/regulatory content detected"
            Draft = $Draft
        }
    }

    if ($TopCategory -eq "Digital Health / Data" -and $TopScore -ge 7) {
        return @{
            Category = $TopCategory
            Bucket = "KEEP - CORE DIGITAL HEALTH"
            Priority = "HIGH"
            Reason = "Strong digital-health/data compliance content detected"
            Draft = $Draft
        }
    }

    if ($TopCategory -eq "PM-JAY / Insurance" -and $TopScore -ge 7) {
        return @{
            Category = $TopCategory
            Bucket = "KEEP - CORE PM-JAY / INSURANCE"
            Priority = "HIGH"
            Reason = "Strong healthcare insurance/PM-JAY content detected"
            Draft = $Draft
        }
    }

    if ($TopCategory -eq "Medical Education / NMC" -and $TopScore -ge 7) {
        return @{
            Category = $TopCategory
            Bucket = "KEEP - MEDICAL REGULATION / EDUCATION"
            Priority = "HIGH"
            Reason = "Strong medical education/professional regulatory content detected"
            Draft = $Draft
        }
    }

    if ($TopScore -ge 5) {
        return @{
            Category = $TopCategory
            Bucket = "KEEP - SUPPORTING GUIDANCE"
            Priority = "MEDIUM"
            Reason = "Relevant official healthcare guidance/content detected"
            Draft = $Draft
        }
    }

    return @{
        Category = $TopCategory
        Bucket = "REVIEW - NEEDS VERIFICATION"
        Priority = "REVIEW"
        Reason = "Some healthcare signal detected but insufficient confidence"
        Draft = $Draft
    }
}

# ------------------------------------------------------------
# Process PDFs
# ------------------------------------------------------------

$Results = @()
$Counter = 0

foreach ($row in $Rows) {

    $Counter++

    Write-Host "[$Counter/$($Rows.Count)] $($row.FileName)"

    $PdfPath = $row.OriginalPath

    # If original path doesn't exist, try locating by filename
    if (-not (Test-Path $PdfPath)) {

        $Found = Get-ChildItem `
            -Path $MasterFolder `
            -Filter $row.FileName `
            -File `
            -Recurse `
            -ErrorAction SilentlyContinue |
            Select-Object -First 1

        if ($Found) {
            $PdfPath = $Found.FullName
        }
    }

    if (-not (Test-Path $PdfPath)) {

        $Results += [PSCustomObject]@{
            FileName = $row.FileName
            FilePath = ""
            DocumentTitle = ""
            Authority = ""
            Year = ""
            SuggestedCategory = ""
            SuggestedBucket = "ERROR - FILE NOT FOUND"
            SuggestedPriority = "REVIEW"
            Reason = "PDF could not be located"
        }

        continue
    }

    # Temporary text file
    $TempText = Join-Path $env:TEMP ("healthcare_" + [guid]::NewGuid().ToString() + ".txt")

    try {

        # Extract first 3 pages only — enough for title/authority/type
        & pdftotext -f 1 -l 3 -layout "$PdfPath" "$TempText" 2>$null

        if (Test-Path $TempText) {
            $Text = Get-Content $TempText -Raw -ErrorAction SilentlyContinue
        }
        else {
            $Text = ""
        }

        if ([string]::IsNullOrWhiteSpace($Text)) {
            $Text = $row.FileName
        }

        # First meaningful lines for title / authority
        $Lines = $Text -split "`r?`n" |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_.Length -gt 2 }

        $Preview = ($Lines | Select-Object -First 12) -join " | "

        # Detect year
        $YearMatch = [regex]::Match($Text, "\b(19|20)\d{2}\b")
        $Year = ""

        if ($YearMatch.Success) {
            $Year = $YearMatch.Value
        }

        # Detect likely authority
        $Authority = ""

        foreach ($AuthorityName in @(
            "Ministry of Health and Family Welfare",
            "Government of India",
            "National Medical Commission",
            "Medical Council of India",
            "National Health Authority",
            "Ayushman Bharat",
            "PM-JAY",
            "Central Drugs Standard Control Organisation",
            "CDSCO",
            "Department of Health",
            "Directorate General of Health Services",
            "Indian Council of Medical Research",
            "ICMR",
            "Bureau of Indian Standards",
            "BIS"
        )) {

            if ($Text -match [regex]::Escape($AuthorityName)) {
                $Authority = $AuthorityName
                break
            }
        }

        $Result = Classify-Text -Text $Text

        $Results += [PSCustomObject]@{
            FileName          = $row.FileName
            FilePath          = $PdfPath
            DocumentTitle     = $Preview
            Authority         = $Authority
            Year              = $Year
            SuggestedCategory = $Result.Category
            SuggestedBucket   = $Result.Bucket
            SuggestedPriority = $Result.Priority
            Reason            = $Result.Reason
        }

    }
    catch {

        $Results += [PSCustomObject]@{
            FileName          = $row.FileName
            FilePath          = $PdfPath
            DocumentTitle     = ""
            Authority         = ""
            Year              = ""
            SuggestedCategory = ""
            SuggestedBucket   = "ERROR - EXTRACTION FAILED"
            SuggestedPriority = "REVIEW"
            Reason            = $_.Exception.Message
        }
    }

    if (Test-Path $TempText) {
        Remove-Item $TempText -Force -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------------------------
# Save
# ------------------------------------------------------------

$Results |
    Export-Csv $OutputFile -NoTypeInformation -Encoding UTF8

# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "       CONTENT CLASSIFICATION COMPLETE" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""

Write-Host "Documents processed: $($Results.Count)"
Write-Host ""

Write-Host "BUCKET SUMMARY"
Write-Host "--------------------------------------------------"

$Results |
    Group-Object SuggestedBucket |
    Sort-Object Count -Descending |
    ForEach-Object {
        Write-Host ("{0,-45} {1,5}" -f $_.Name, $_.Count)
    }

Write-Host ""
Write-Host "PRIORITY SUMMARY"
Write-Host "--------------------------------------------------"

$Results |
    Group-Object SuggestedPriority |
    Sort-Object Name |
    ForEach-Object {
        Write-Host ("{0,-15} {1,5}" -f $_.Name, $_.Count)
    }

Write-Host ""
Write-Host "Output:"
Write-Host $OutputFile
Write-Host ""