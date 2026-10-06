<#
.SYNOPSIS
    Run the test suite.
.DESCRIPTION
    The suite needs no database server and never loads the OCR engine: it runs
    against in-memory SQLite with drawn table images and OCR boxes placed at the
    coordinates the real recogniser returned.
.EXAMPLE
    .\scripts\test.ps1
    .\scripts\test.ps1 tests\test_balance_parser.py
#>
[CmdletBinding()]
param(
    [string]$Target = "tests"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

& $python -m pytest $Target -q --ds=config.settings.sqlite
