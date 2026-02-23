# Run driver tests.
# Usage:
#   .\run_tests.ps1              — unit + property tests only (fast, no hardware)
#   .\run_tests.ps1 -Simulator   — also run simulator integration tests
#
# Unit/property tests run without device.
# Simulator tests start main-2.py + main.py as subprocesses automatically.

param(
    [switch]$Simulator
)

$repoRoot = $PSScriptRoot
$driverDir = Join-Path $repoRoot "drivers\dryve-d1-driver"

# ── 1. Main-project unit tests ────────────────────────
Write-Host "=== Running drivers/tests/unit/ (main project) ===" -ForegroundColor Cyan
Push-Location $repoRoot
try {
    python -m pytest drivers/tests/unit/ -v --tb=short
    $mainResult = $LASTEXITCODE
} finally {
    Pop-Location
}

# ── 2. Package unit + property tests ──────────────────
Write-Host ""
Write-Host "=== Running drivers/dryve-d1-driver/tests/ (package) ===" -ForegroundColor Cyan
Push-Location $repoRoot
try {
    python -m pytest drivers/dryve-d1-driver/tests/unit/ drivers/dryve-d1-driver/tests/property/ -v --tb=short
    $pkgResult = $LASTEXITCODE
} finally {
    Pop-Location
}

# ── 3. Simulator integration tests (optional) ────────
$simResult = 0
if ($Simulator) {
    Write-Host ""
    Write-Host "=== Running simulator integration tests ===" -ForegroundColor Cyan
    Push-Location $repoRoot
    try {
        python -m pytest drivers/tests/integration/test_simulator_api.py -v --tb=short -m simulator
        $simResult = $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

# ── Summary ───────────────────────────────────────────
Write-Host ""
$allOk = ($mainResult -eq 0) -and ($pkgResult -eq 0) -and ($simResult -eq 0)
if ($allOk) {
    Write-Host "ALL TEST SUITES PASSED" -ForegroundColor Green
} else {
    Write-Host "SOME TEST SUITES FAILED" -ForegroundColor Red
    if ($mainResult -ne 0) { Write-Host "  drivers/tests/unit/ failed (exit $mainResult)" -ForegroundColor Red }
    if ($pkgResult -ne 0)  { Write-Host "  dryve-d1-driver/tests/ failed (exit $pkgResult)" -ForegroundColor Red }
    if ($simResult -ne 0)  { Write-Host "  simulator integration failed (exit $simResult)" -ForegroundColor Red }
    exit 1
}
