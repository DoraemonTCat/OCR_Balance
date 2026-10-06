<#
.SYNOPSIS
    Create the local virtual environment and install dependencies.
.DESCRIPTION
    Python 3.10 - 3.12 only: paddlepaddle 2.6.2 publishes no wheel for 3.13.
.EXAMPLE
    .\scripts\setup.ps1
    .\scripts\setup.ps1 -Dev
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [string]$Python = "py -3.10"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating .venv with $Python..."
    Invoke-Expression "$Python -m venv .venv"
}

$python = Join-Path $root ".venv\Scripts\python.exe"
& $python -m pip install --upgrade pip

$requirements = if ($Dev) { "requirements-dev.txt" } else { "requirements.txt" }
Write-Host "Installing $requirements (PaddleOCR is large; this takes a while)..."
& $python -m pip install -r $requirements

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created from .env.example - set DB_PASSWORD and DJANGO_SECRET_KEY."
}

Write-Host "Building the CA bundle (needed if this machine inspects TLS)..."
& $python scripts\setup_certs.py

Write-Host "Done."
