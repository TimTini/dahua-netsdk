param(
    [string]$User = $env:DAHUA_USER,
    [string]$Password = $env:DAHUA_PASS,
    [string]$Serial = $env:DAHUA_SERIAL,
    [int]$Channel = 2,
    [string]$P2pServer = "easy4ip"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& .\scripts\ensure.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $Password) {
    $sec = Read-Host "Password" -AsSecureString
    $Password = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
if (-not $Serial) { throw "Cần DAHUA_SERIAL hoặc -Serial" }

.\.venv\Scripts\Activate.ps1
python view_cam2.py -u $User -p $Password --serial $Serial --channel $Channel --p2p-server $P2pServer
