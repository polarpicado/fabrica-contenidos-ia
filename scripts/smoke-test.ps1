param(
  [string]$LocalBase = 'http://localhost:8083',
  [string]$PublicBase = 'https://collins-travis-discrimination-villages.trycloudflare.com'
)

$ErrorActionPreference = 'Stop'

function Test-Url($url) {
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri $url -Method GET -TimeoutSec 20
    return @{ ok = $true; code = $r.StatusCode; len = ($r.Content | Out-String).Length }
  } catch {
    return @{ ok = $false; code = -1; err = $_.Exception.Message }
  }
}

Write-Host "[1/5] Local web" -ForegroundColor Cyan
$l1 = Test-Url "$LocalBase/"
$l1 | ConvertTo-Json -Compress | Write-Host

Write-Host "[2/5] Local API health" -ForegroundColor Cyan
$l2 = Test-Url "$LocalBase/api/health"
$l2 | ConvertTo-Json -Compress | Write-Host

Write-Host "[3/5] Local outputs index" -ForegroundColor Cyan
$l3 = Test-Url "$LocalBase/api/outputs-index"
$l3 | ConvertTo-Json -Compress | Write-Host

Write-Host "[4/5] Public web" -ForegroundColor Cyan
$p1 = Test-Url "$PublicBase/"
$p1 | ConvertTo-Json -Compress | Write-Host

Write-Host "[5/5] Public outputs index" -ForegroundColor Cyan
$p2 = Test-Url "$PublicBase/api/outputs-index"
$p2 | ConvertTo-Json -Compress | Write-Host

$ok = @($l1,$l2,$l3,$p1,$p2) | Where-Object { $_.ok -eq $true }
if ($ok.Count -eq 5) {
  Write-Host "SMOKE TEST: OK" -ForegroundColor Green
  exit 0
}

Write-Host "SMOKE TEST: FAIL" -ForegroundColor Red
exit 1
