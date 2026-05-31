# Chuẩn bị repo: venv, vendor/sdk, vendor/bin/ffmpeg
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
& $py bootstrap.py
exit $LASTEXITCODE
