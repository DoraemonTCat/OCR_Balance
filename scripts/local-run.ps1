<#
.SYNOPSIS
    Run the API locally.
.DESCRIPTION
    Uses the database in .env. Pass -Sqlite to run without a PostgreSQL server;
    that mode keeps its data in data\local.sqlite3.
.EXAMPLE
    .\scripts\local-run.ps1            # http://localhost:3004
    .\scripts\local-run.ps1 -Sqlite
#>
[CmdletBinding()]
param(
    [switch]$Sqlite,
    [int]$Port = 3004
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if ($Sqlite) {
    & $python manage.py runserver "0.0.0.0:$Port" --settings=config.settings.local_sqlite
} else {
    & $python manage.py runserver "0.0.0.0:$Port"
}
