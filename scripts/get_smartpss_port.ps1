param([string]$LogDir = "")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

if ($LogDir) {
    $resolved = $LogDir
} else {
    & .\scripts\ensure.ps1 | Out-Null
    $resolved = & $py -c "from paths import resolve_smartpss_log_dir; print(resolve_smartpss_log_dir())"
}

$log = Get-ChildItem $resolved -Filter "*.log" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $log) { Write-Error "Không thấy log trong $resolved"; exit 1 }

$line = Select-String -Path $log.FullName -Pattern "iLocalPort\s*=\s*(\d+)" | Select-Object -Last 1
if (-not $line) { Write-Error "Chưa có iLocalPort — mở SmartPSS, đợi device online."; exit 1 }

[int]$line.Matches[0].Groups[1].Value
