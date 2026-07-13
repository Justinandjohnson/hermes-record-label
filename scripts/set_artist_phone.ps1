# Usage: .\scripts\set_artist_phone.ps1 +15551234567
param(
    [Parameter(Mandatory=$true)]
    [string]$Number
)

$HermesProfiles = Join-Path $env:USERPROFILE ".hermes\profiles"

foreach ($profile in @("a_and_r", "manager", "creative_director", "bandcamp")) {
    $envFile = Join-Path $HermesProfiles "$profile\.env"
    if (Test-Path $envFile) {
        $content = Get-Content $envFile -Raw
        $content = $content -replace "ARTIST_PHONE=.*", "ARTIST_PHONE=$Number"
        $content = $content -replace "SMS_ALLOWED_USERS=.*", "SMS_ALLOWED_USERS=$Number"
        $content = $content -replace "SMS_HOME_CHANNEL=.*", "SMS_HOME_CHANNEL=$Number"
        Set-Content -Path $envFile -Value $content
        Write-Host "  ✓ $profile → $Number" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Done. Restart gateways to pick up the change." -ForegroundColor Cyan
