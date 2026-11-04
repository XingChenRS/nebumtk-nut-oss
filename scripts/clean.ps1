param(
    [switch]$IncludeRelease
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$paths = @(
    ".pytest_cache",
    ".pytest-unused",
    "build",
    "nebumtk_unlock_tools.egg-info",
    "NEBUMTK_UNLOCK_TOOLS.build",
    "NEBUMTK_UNLOCK_TOOLS.onefile-build",
    "nebumtk_unlock_tools.build",
    "__main__.build",
    "dist\NEBUMTK_UNLOCK_TOOLS.build",
    "dist\NEBUMTK_UNLOCK_TOOLS.onefile-build",
    "dist\nebumtk_unlock_tools.build",
    "dist\__main__.build"
)

if ($IncludeRelease) {
    $paths += @(
        "dist",
        "NEBUMTK_UNLOCK_TOOLS.dist",
        "nebumtk_unlock_tools.dist",
        "__main__.dist"
    )
}

foreach ($name in $paths) {
    $target = Join-Path $Root $name
    if (-not (Test-Path $target)) { continue }
    Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $target) {
        Write-Host "WARN: could not fully remove $name (close running exe first)" -ForegroundColor Yellow
    } else {
        Write-Host "Removed $name"
    }
}

foreach ($name in @("nuitka-crash-report.xml", "aqtinstall.log")) {
    $target = Join-Path $Root $name
    if (Test-Path $target) {
        Remove-Item $target -Force
        Write-Host "Removed $name"
    }
}

Get-ChildItem $Root -Filter "*.spec" -File -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item $_.FullName -Force
    Write-Host "Removed $($_.Name)"
}

Get-ChildItem $Root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item $_.FullName -Recurse -Force
}

Write-Host "Clean done"
if (-not $IncludeRelease) {
    Write-Host "Current dist release preserved. Use -IncludeRelease to remove release outputs." -ForegroundColor Cyan
}
