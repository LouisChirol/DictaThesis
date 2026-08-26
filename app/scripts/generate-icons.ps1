# Regenerate HUD / tray / window icons from assets/turgot_dictating.png
# Requires ffmpeg on PATH. Run from repo root:
#   powershell -File app/scripts/generate-icons.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Source = Join-Path $Root "assets\turgot_dictating.png"
$OutDir = Join-Path $Root "app\src\renderer\assets"

if (-not (Test-Path $Source)) {
    Write-Error "Missing source image: $Source"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$Filter = "scale={0}:{0}:force_original_aspect_ratio=increase:flags=lanczos,crop={0}:{0}"

function Export-Icon($size, $name) {
    $vf = $Filter -f $size
    $dest = Join-Path $OutDir $name
    & ffmpeg -y -loglevel error -i $Source -vf $vf $dest
    Write-Host "Wrote $name (${size}x${size})"
}

# HUD avatar: 3x the 28px CSS size for crisp downscale
Export-Icon 96 "turgot-avatar.png"
# Window / taskbar (Electron)
Export-Icon 512 "icon.png"
# Tray: native 32px — do not downscale in code
Export-Icon 32 "tray-icon.png"
# Optional 2x tray for HiDPI
Export-Icon 64 "tray-icon@2x.png"

Write-Host "Done. Rebuild app: cd app && npm run build"
