<#
.SYNOPSIS
    Install Tesseract and the Thai models it needs to read buyers' names.
.DESCRIPTION
    Tesseract is the second reader of the Thai names on the sale ledgers (see
    workers/thai_names.py). It is optional - without it the names are read by
    PaddleOCR alone - but on ต.ย. OCR.pdf it lifts whole-name accuracy a lot.

    1. Installs Tesseract 5.4 for Windows (UB Mannheim build) unless it is
       already installed. The installer asks for administrator rights and goes
       to C:\Program Files\Tesseract-OCR, where workers/tesseract_engine.py
       looks for it if it is not on PATH.
    2. Downloads the Thai models into .\tessdata (TESSDATA_DIR):
         tha.traineddata       - tessdata_best, the accurate model
         tha_fast.traineddata  - tessdata_fast, a second opinion
.EXAMPLE
    .\scripts\setup_tesseract.ps1
    .\scripts\setup_tesseract.ps1 -SkipInstall   # models only
#>
[CmdletBinding()]
param([switch]$SkipInstall)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
$exe = "C:\Program Files\Tesseract-OCR\tesseract.exe"

if (-not $SkipInstall -and -not (Test-Path $exe) -and -not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    $version = "5.4.0.20240606"
    $installer = Join-Path $env:TEMP "tesseract-ocr-w64-setup-$version.exe"
    Write-Host "Downloading Tesseract $version..."
    Invoke-WebRequest -Uri "https://github.com/UB-Mannheim/tesseract/releases/download/v$version/tesseract-ocr-w64-setup-$version.exe" -OutFile $installer
    $signature = Get-AuthenticodeSignature $installer
    if ($signature.SignerCertificate.Subject -notmatch "Mannheim") {
        throw "Unexpected signer on the Tesseract installer: $($signature.SignerCertificate.Subject)"
    }
    Write-Host "Installing (silent)..."
    Start-Process -FilePath $installer -ArgumentList "/S" -Wait
}

$tessdata = Join-Path $root "tessdata"
New-Item -ItemType Directory -Force $tessdata | Out-Null
$models = @{
    "tha.traineddata"      = "https://github.com/tesseract-ocr/tessdata_best/raw/main/tha.traineddata"
    "tha_fast.traineddata" = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/tha.traineddata"
}
foreach ($name in $models.Keys) {
    $target = Join-Path $tessdata $name
    if (-not (Test-Path $target)) {
        Write-Host "Downloading $name..."
        Invoke-WebRequest -Uri $models[$name] -OutFile $target
    }
}
Write-Host "Done. Models in $tessdata"
