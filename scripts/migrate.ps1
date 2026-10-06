<#
.SYNOPSIS
    Create this service's tables.
.DESCRIPTION
    Only balance_* and django_* are created; the service shares a server with
    anything else without touching its tables.
.EXAMPLE
    .\scripts\migrate.ps1
    .\scripts\migrate.ps1 -Plan
    .\scripts\migrate.ps1 -Sqlite
#>
[CmdletBinding()]
param(
    [switch]$Plan,
    [switch]$Sqlite,
    [string]$Database
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if ($Database) { $env:DB_NAME = $Database }

$arguments = @("manage.py", "migrate")
if ($Plan) { $arguments += "--plan" } else { $arguments += "--noinput" }
if ($Sqlite) { $arguments += "--settings=config.settings.local_sqlite" }

& $python @arguments
