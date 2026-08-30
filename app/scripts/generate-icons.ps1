# Regenerate icons from assets/turgot_dictating.png via SVG trace (preferred).
# Requires: npm install in app/ (potrace + @resvg/resvg-js)
# Run from repo root: powershell -File app/scripts/generate-icons.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Push-Location (Join-Path $Root "app")
try {
    node scripts/vectorize-icon.mjs
} finally {
    Pop-Location
}
