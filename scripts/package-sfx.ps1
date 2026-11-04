param(
    [string]$DistributionRoot = "",
    [string]$OutputPath = "",
    [switch]$Force
)

# Package an already audited Nuitka standalone directory into a self-extracting
# archive. The open-source build ships no VMProtect stage and no archive
# password: protection layers are not part of this repository.

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$DistRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "dist"))
if (-not $DistributionRoot) {
    $DistributionRoot = Join-Path $DistRoot "nebumtk_unlock_tools.dist"
}
if (-not $OutputPath) {
    $OutputPath = Join-Path $DistRoot "NEBUMTK_OFFLINE_SFX.exe"
}
$DistributionRoot = [System.IO.Path]::GetFullPath($DistributionRoot)
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)

foreach ($Path in @($DistributionRoot, $OutputPath)) {
    if (-not $Path.StartsWith(
        $DistRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "SFX inputs and output must stay under: $DistRoot"
    }
}
foreach ($CriticalPath in @($DistRoot, $DistributionRoot, (Split-Path -Parent $OutputPath), $OutputPath)) {
    $Current = $CriticalPath
    while ($Current -and $Current.StartsWith($DistRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $Current) {
            $Item = Get-Item -LiteralPath $Current -Force
            if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "SFX packaging refuses a reparse point or junction: $Current"
            }
        }
        if ($Current.Equals($DistRoot, [System.StringComparison]::OrdinalIgnoreCase)) { break }
        $Current = Split-Path -Parent $Current
    }
}
if (-not (Test-Path -LiteralPath $DistributionRoot -PathType Container)) {
    throw "Distribution directory is missing: $DistributionRoot"
}
if (-not [System.IO.Path]::GetExtension($OutputPath).Equals(
    ".exe",
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "SFX output must be an .exe file"
}
if ($OutputPath.StartsWith(
    $DistributionRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "SFX output must stay outside the distribution directory"
}
if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
    throw "SFX output already exists; use -Force or choose another path: $OutputPath"
}

$WinRarCandidates = @(
    (Join-Path $env:ProgramFiles "WinRAR\WinRAR.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "WinRAR\WinRAR.exe")
)
$WinRar = $WinRarCandidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
    Select-Object -First 1
if (-not $WinRar) {
    throw "WinRAR.exe not found"
}
$Rar = Join-Path (Split-Path $WinRar -Parent) "Rar.exe"
if (-not (Test-Path -LiteralPath $Rar)) {
    throw "Rar.exe not found next to WinRAR.exe"
}

foreach ($Required in @(
    "NEBUMTK_UNLOCK_TOOLS.exe",
    "release-sha256-manifest.json",
    "platform-tools\adb.exe",
    "platform-tools\fastboot.exe"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $DistributionRoot $Required))) {
        throw "Distribution is incomplete: $Required"
    }
}
if (Test-Path -LiteralPath (Join-Path $DistributionRoot "devices.yaml")) {
    throw "Distribution contains forbidden editable catalog: devices.yaml"
}
# Re-audit the exact directory immediately before archiving. This prevents a
# stale or modified distribution from being wrapped using an older manifest.
python (Join-Path $ProjectRoot "tools\audit_distribution.py") $DistributionRoot
if ($LASTEXITCODE -ne 0) {
    throw "Distribution audit failed immediately before SFX packaging"
}

$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "NEBUMTK_SFX_" + [Guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $TempRoot | Out-Null
$CommentPath = Join-Path $TempRoot "sfx-comment.txt"
$ExtractRoot = Join-Path $TempRoot "extracted"
$OutputDirectory = Split-Path -Parent $OutputPath
$TempOutput = Join-Path $OutputDirectory (
    "." + [System.IO.Path]::GetFileNameWithoutExtension($OutputPath) + "." +
    [Guid]::NewGuid().ToString("N") + ".tmp.exe"
)
$ReplacementBackup = Join-Path $OutputDirectory (
    "." + [System.IO.Path]::GetFileName($OutputPath) + "." +
    [Guid]::NewGuid().ToString("N") + ".replace-backup"
)
$SfxComment = @"
;The comment below contains SFX script commands
Title=NEBUMTK Unlock Tools
TempMode
Setup=NEBUMTK_UNLOCK_TOOLS.exe
Silent=1
Overwrite=1
"@
[System.IO.File]::WriteAllText(
    $CommentPath,
    $SfxComment,
    [System.Text.UTF8Encoding]::new($false)
)

try {
    Push-Location $DistributionRoot
    try {
        & $Rar a -r -ep1 -ma5 -m5 -md128m -sfx "-z$CommentPath" -y -idq $TempOutput "*"
        if ($LASTEXITCODE -ne 0) {
            throw "WinRAR SFX creation failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $TempOutput -PathType Leaf)) {
        throw "WinRAR reported success but SFX output is missing"
    }
    & $Rar t -idq $TempOutput
    if ($LASTEXITCODE -ne 0) {
        throw "SFX archive verification failed with exit code $LASTEXITCODE"
    }
    New-Item -ItemType Directory -Path $ExtractRoot | Out-Null
    & $Rar x -idq -y $TempOutput ($ExtractRoot + "\")
    if ($LASTEXITCODE -ne 0) {
        throw "SFX extraction verification failed with exit code $LASTEXITCODE"
    }
    python (Join-Path $ProjectRoot "tools\audit_distribution.py") $ExtractRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Extracted SFX distribution audit failed"
    }
    # Publish only after the temporary SFX has passed archive, extraction and
    # full distribution verification. -Force never removes a previously good
    # package before the replacement has been proven.
    if (Test-Path -LiteralPath $OutputPath) {
        if (-not $Force) {
            throw "SFX output appeared during packaging; refusing to replace it without -Force: $OutputPath"
        }
        [System.IO.File]::Replace($TempOutput, $OutputPath, $ReplacementBackup, $true)
        Remove-Item -LiteralPath $ReplacementBackup -Force
    } else {
        Move-Item -LiteralPath $TempOutput -Destination $OutputPath
    }
    Write-Output $OutputPath
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $TempOutput) {
        Remove-Item -LiteralPath $TempOutput -Force
    }
}
