# Reclassification - 23 demotions, no promotions. Generated 2026-09-27.
$ErrorActionPreference = "Continue"
Set-Location "C:\Users\rahma\OneDrive - Indian Institute of Management\Desktop\Term 5\CCS\RAG_Project\02_Corpus\Healthcare"
$moved = 0; $missing = 0

# Hardware Specification for AB-NHPM Empanelled Hospitals
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\01_Core_Regulatory\2022051026_62a1b010.pdf") {
  Move-Item -LiteralPath ".\01_Core_Regulatory\2022051026_62a1b010.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2022051026_62a1b010.pdf" -ForegroundColor Yellow; $missing++ }

# Whistle Blower Policy - Ayushman Bharat Pradhan Mantri Jan Arogya Yojana
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\01_Core_Regulatory\2022051036-1_a2a62f5c.pdf") {
  Move-Item -LiteralPath ".\01_Core_Regulatory\2022051036-1_a2a62f5c.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2022051036-1_a2a62f5c.pdf" -ForegroundColor Yellow; $missing++ }

# RD Services - Registered Biometric Devices for Aadhaar Authentication (AB-NHPM)
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\01_Core_Regulatory\2022051078_30d94d70.pdf") {
  Move-Item -LiteralPath ".\01_Core_Regulatory\2022051078_30d94d70.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2022051078_30d94d70.pdf" -ForegroundColor Yellow; $missing++ }

# Guidance for National Institutes of Excellence (NIE) under AB PM-JAY
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\01_Core_Regulatory\2022051095-2_dc2bf0b9.pdf") {
  Move-Item -LiteralPath ".\01_Core_Regulatory\2022051095-2_dc2bf0b9.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2022051095-2_dc2bf0b9.pdf" -ForegroundColor Yellow; $missing++ }

# Hospital Empanelment Guidelines for PM-RAHAT (Prime Minister's Road Accident Victims' Hospitalis
#   reason: page 1 carries an unfilled placeholder date (ISSUE DATE: XX/XX/XX) - unissued draft
if (Test-Path -LiteralPath ".\01_Core_Regulatory\202609031776794593_3c961873.pdf") {
  Move-Item -LiteralPath ".\01_Core_Regulatory\202609031776794593_3c961873.pdf" -Destination ".\05_Draft_Proposed\" -Force; $moved++
} else { Write-Host "MISSING: 202609031776794593_3c961873.pdf" -ForegroundColor Yellow; $missing++ }

# 1. FAQs on Recognition Regulation 2023 dated 05.01.2024
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\01_Core_Regulatory\3.1. FAQs on Recognition Regulation 2023 dated 05.01.2024_637a257a.pdf") {
  Move-Item -LiteralPath ".\01_Core_Regulatory\3.1. FAQs on Recognition Regulation 2023 dated 05.01.2024_637a257a.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 3.1. FAQs on Recognition Regulation 2023 dated 05.01.2024_637a257a.pdf" -ForegroundColor Yellow; $missing++ }

# 2 Frequently Asked Questions on FMGL Regulations dated 22nd February, 2022
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\01_Core_Regulatory\5.2 Frequently Asked Questions on FMGL Regulations dated 22nd February, 2022_f9ad1c9d.pdf") {
  Move-Item -LiteralPath ".\01_Core_Regulatory\5.2 Frequently Asked Questions on FMGL Regulations dated 22nd February, 2022_f9ad1c9d.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 5.2 Frequently Asked Questions on FMGL Regulations dated 22nd February, 2022_f9ad1c9d.pdf" -ForegroundColor Yellow; $missing++ }

# 3 Frequently Asked Questions on Screening Test Regulations dated 22nd February, 2022
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\01_Core_Regulatory\5.3 Frequently Asked Questions on Screening Test Regulations dated 22nd February, 2022_5402c0f1.pdf") {
  Move-Item -LiteralPath ".\01_Core_Regulatory\5.3 Frequently Asked Questions on Screening Test Regulations dated 22nd February, 2022_5402c0f1.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 5.3 Frequently Asked Questions on Screening Test Regulations dated 22nd February, 2022_5402c0f1.pdf" -ForegroundColor Yellow; $missing++ }

# Standard Treatment Guidelines: Management of Dry Eye Disease in India - Quick Reference Guide
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\01_Core_Regulatory\b) Quick Reference Guide_98a2299e.pdf") {
  Move-Item -LiteralPath ".\01_Core_Regulatory\b) Quick Reference Guide_98a2299e.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: b) Quick Reference Guide_98a2299e.pdf" -ForegroundColor Yellow; $missing++ }

# Fraud Investigation and Medical Audit Manual - AB PM-JAY
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\2022051018-1_0801124f.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\2022051018-1_0801124f.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2022051018-1_0801124f.pdf" -ForegroundColor Yellow; $missing++ }

# Claims Adjudication Manual - Ayushman Bharat PM-JAY (NHA, February 2019)
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\2022051036-2_84903cca.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\2022051036-2_84903cca.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2022051036-2_84903cca.pdf" -ForegroundColor Yellow; $missing++ }

# Streamlining Systems and Processes at Hospitals - AB PM-JAY
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\2022051054-1_45c044e9.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\2022051054-1_45c044e9.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2022051054-1_45c044e9.pdf" -ForegroundColor Yellow; $missing++ }

# Ayushman Bharat PM-JAY Beneficiary Empowerment Guidebook
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\2022051067_9f475305.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\2022051067_9f475305.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2022051067_9f475305.pdf" -ForegroundColor Yellow; $missing++ }

# Guidelines for Partnership with Resource Organisations for Augmenting Capacity for the National 
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\2022051078-1_84a2ae77.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\2022051078-1_84a2ae77.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2022051078-1_84a2ae77.pdf" -ForegroundColor Yellow; $missing++ }

# Anti-Fraud Framework Practitioners' Guidebook - Ayushman Bharat PM-JAY
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\20240924831436164_2c5eded9.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\20240924831436164_2c5eded9.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 20240924831436164_2c5eded9.pdf" -ForegroundColor Yellow; $missing++ }

# Claims Adjudication FAQs - AB PM-JAY
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\20240925185541293_7ce7d80b.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\20240925185541293_7ce7d80b.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 20240925185541293_7ce7d80b.pdf" -ForegroundColor Yellow; $missing++ }

# AB PM-JAY scheme document, November 2024 - title not stated on first page; VERIFY
#   reason: title could not be established - binding status unverified
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\20241104222942183_6cf631d2.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\20241104222942183_6cf631d2.pdf" -Destination ".\06_Review\" -Force; $moved++
} else { Write-Host "MISSING: 20241104222942183_6cf631d2.pdf" -ForegroundColor Yellow; $missing++ }

# Compendium - Ayushman Bharat Pradhan Mantri Jan Arogya Yojana (July 2026)
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\202609031194350508_70532cc3.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\202609031194350508_70532cc3.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 202609031194350508_70532cc3.pdf" -ForegroundColor Yellow; $missing++ }

# Institutionalizing Performance Linked Payments (PLP) for Community Health Officers and Frontline
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\6381b33c4d25c4363679438afea500af_c583d416.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\6381b33c4d25c4363679438afea500af_c583d416.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 6381b33c4d25c4363679438afea500af_c583d416.pdf" -ForegroundColor Yellow; $missing++ }

# Background Note - Pradhan Mantri Rashtriya Swasthya Suraksha Mission (PMRSSM)
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\Background note.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\Background note.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: Background note.pdf" -ForegroundColor Yellow; $missing++ }

# INFORMATION, EDUCATION AND COMMUNICATION (IEC)
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\Download.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\Download.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: Download.pdf" -ForegroundColor Yellow; $missing++ }

# REFERENCE MANUAL Facade branding for
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\02_Core_PMJAY_Insurance\cdc59be4f64be9217515d5c5cf618da9_f2bd4918.pdf") {
  Move-Item -LiteralPath ".\02_Core_PMJAY_Insurance\cdc59be4f64be9217515d5c5cf618da9_f2bd4918.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: cdc59be4f64be9217515d5c5cf618da9_f2bd4918.pdf" -ForegroundColor Yellow; $missing++ }

# 1. FAQs on PGMER-2023 dated 10.04.2024
#   reason: explanatory or operational document, not binding regulation
if (Test-Path -LiteralPath ".\03_Medical_Regulation_Education\2.1. FAQs on PGMER-2023 dated 10.04.2024_3024dcc0.pdf") {
  Move-Item -LiteralPath ".\03_Medical_Regulation_Education\2.1. FAQs on PGMER-2023 dated 10.04.2024_3024dcc0.pdf" -Destination ".\04_Supporting_Guidance\" -Force; $moved++
} else { Write-Host "MISSING: 2.1. FAQs on PGMER-2023 dated 10.04.2024_3024dcc0.pdf" -ForegroundColor Yellow; $missing++ }

Write-Host ""
Write-Host "moved   : $moved" -ForegroundColor Green
Write-Host "missing : $missing"
Write-Host ""
foreach ($b in @("01_Core_Regulatory","02_Core_PMJAY_Insurance","03_Medical_Regulation_Education","04_Supporting_Guidance","05_Draft_Proposed","06_Review")) {
  "{0,-36} {1,4}" -f $b, (Get-ChildItem ".\$b" -Filter *.pdf).Count }