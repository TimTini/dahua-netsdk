$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

& .\scripts\ensure.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\Activate.ps1
$Out = Join-Path $PSScriptRoot "downloads\clip_cam2_1min.mp4"
$Pass = $env:DAHUA_PASS
if (-not $Pass) {
    $sec = Read-Host "Password" -AsSecureString
    $Pass = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}

if ($env:DAHUA_LAN_IP) {
    python playback_clip.py -p $Pass --host $env:DAHUA_LAN_IP --channel 2 -o $Out
    if ($LASTEXITCODE -eq 0 -and (Test-Path $Out) -and (Get-Item $Out).Length -gt 1000) {
        Start-Process $Out
        exit 0
    }
}

try {
    $port = .\scripts\get_smartpss_port.ps1
    python netsdk_download.py -p $Pass --ip 127.0.0.1 --port $port --channel-ui 2 -o $Out --mp4
    if ($LASTEXITCODE -eq 0) { Start-Process $Out; exit 0 }
} catch {
    Write-Host $_
}
exit 1
