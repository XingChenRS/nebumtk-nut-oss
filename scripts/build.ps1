param(
    [switch]$SkipInstall,
    [switch]$OneFile,
    [switch]$SkipLaunchSmoke,
    [ValidateSet("Offline")]
    [string]$Channel = "Offline",
    [int]$SmokeTimeoutSeconds = 10
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (-not $SkipInstall) {
    pip install -r requirements.txt
    pip install nuitka ordered-set zstandard
}

if ($OneFile) {
    Write-Host "Nuitka onefile build (slower startup; extracts to temp)..." -ForegroundColor Yellow
} else {
    Write-Host "Nuitka standalone build (recommended for production)..." -ForegroundColor Yellow
}

$extra = @()
if ($OneFile) {
    $extra += "--onefile"
}

Write-Host "Build channel: $Channel" -ForegroundColor Cyan
$ExpectedChannel = $Channel.ToLowerInvariant()
python -c "import sys; from nebumtk_unlock_tools.build_profile import BUILD_CHANNEL; expected=sys.argv[1]; actual=str(BUILD_CHANNEL).strip().lower(); print(f'Build profile channel: {actual}'); raise SystemExit(0 if actual == expected else f'build channel mismatch: source={actual}, requested={expected}')" $ExpectedChannel
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build profile does not match requested release channel" -ForegroundColor Red
    exit $LASTEXITCODE
}

python tools/build_device_catalog.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Signed device catalog validation failed" -ForegroundColor Red
    exit $LASTEXITCODE
}

if ($Channel -eq "Offline") {
    # The open-source build carries no embedded payload ciphertext: device
    # payloads live in the operator's local material store (material/payloads).
    $StoreManifest = Join-Path $Root "material\payloads\manifest.json"
    if (Test-Path -LiteralPath $StoreManifest) {
        python tools/embed_preloads.py
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Preload store validation failed with exit code $LASTEXITCODE" -ForegroundColor Red
            exit $LASTEXITCODE
        }
    } else {
        Write-Host "No local preload store; building without device payload material" -ForegroundColor Yellow
    }
}

python -m nuitka `
    --standalone `
    --zig `
    @extra `
    --windows-console-mode=disable `
    --enable-plugin=pyside6 `
    --include-data-dir=misc=misc `
    --output-dir=dist `
    --output-filename=NEBUMTK_UNLOCK_TOOLS.exe `
    --remove-output `
    --assume-yes-for-downloads `
    --python-flag=-m `
    --python-flag=no_docstrings `
    --python-flag=isolated `
    --python-flag=safe_path `
    nebumtk_unlock_tools

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

if ($OneFile) {
    $CompiledExePath = Join-Path $Root "dist\NEBUMTK_UNLOCK_TOOLS.exe"
    $DistributionRoot = Join-Path $Root "dist\NEBUMTK_UNLOCK_TOOLS.onefile"
    $ResolvedDistRoot = [System.IO.Path]::GetFullPath((Join-Path $Root "dist"))
    $ResolvedDistributionRoot = [System.IO.Path]::GetFullPath($DistributionRoot)
    if (-not $ResolvedDistributionRoot.StartsWith($ResolvedDistRoot + [System.IO.Path]::DirectorySeparatorChar)) {
        Write-Host "Refusing unsafe onefile distribution target: $ResolvedDistributionRoot" -ForegroundColor Red
        exit 1
    }
    if (Test-Path $DistributionRoot) {
        Remove-Item -LiteralPath $DistributionRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $DistributionRoot | Out-Null
    $ExePath = Join-Path $DistributionRoot "NEBUMTK_UNLOCK_TOOLS.exe"
    Move-Item -LiteralPath $CompiledExePath -Destination $ExePath -Force
} else {
    $ExePath = Join-Path $Root "dist\nebumtk_unlock_tools.dist\NEBUMTK_UNLOCK_TOOLS.exe"
}
if (-not (Test-Path $ExePath)) {
    Write-Host "Build finished but expected exe not found: $ExePath" -ForegroundColor Red
    Get-ChildItem (Join-Path $Root "dist") -Recurse -Filter "NEBUMTK_UNLOCK_TOOLS.exe" | ForEach-Object { $_.FullName }
    exit 1
}

$PlatformToolsSource = Join-Path $Root "platform-tools"
$PlatformToolsTarget = Join-Path (Split-Path $ExePath -Parent) "platform-tools"
if (-not (Test-Path (Join-Path $PlatformToolsSource "adb.exe")) -or
    -not (Test-Path (Join-Path $PlatformToolsSource "fastboot.exe"))) {
    Write-Host "platform-tools source is missing adb.exe or fastboot.exe" -ForegroundColor Red
    exit 1
}
if (Test-Path $PlatformToolsTarget) {
    Remove-Item $PlatformToolsTarget -Recurse -Force
}
Copy-Item $PlatformToolsSource $PlatformToolsTarget -Recurse -Force
if (-not (Test-Path (Join-Path $PlatformToolsTarget "adb.exe")) -or
    -not (Test-Path (Join-Path $PlatformToolsTarget "fastboot.exe"))) {
    Write-Host "Failed to copy platform-tools next to built exe" -ForegroundColor Red
    exit 1
}

$LibusbPackage = python -c "import libusb; print(libusb.__file__)"
$LibusbRoot = Join-Path (Split-Path $LibusbPackage -Parent) "_platform\_windows\x64"
$LibusbSource = Join-Path $LibusbRoot "libusb-1.0.dll"
$LibusbTarget = Join-Path (Split-Path $ExePath -Parent) "nebumtk_unlock_tools\lk\libusb\_platform\_windows\x64\libusb-1.0.dll"
if (-not (Test-Path $LibusbSource)) {
    Write-Host "libusb-1.0.dll source not found: $LibusbSource" -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force -Path (Split-Path $LibusbTarget -Parent) | Out-Null
Copy-Item $LibusbSource $LibusbTarget -Force

$DistributionRoot = Split-Path $ExePath -Parent
[System.IO.File]::WriteAllText(
    (Join-Path $DistributionRoot "release-channel.txt"),
    $Channel.ToLowerInvariant() + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)

python tools/audit_distribution.py --write-manifest $DistributionRoot
if ($LASTEXITCODE -ne 0) {
    Write-Host "Distribution protection audit failed" -ForegroundColor Red
    exit $LASTEXITCODE
}

if (-not $SkipLaunchSmoke) {
    $TempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $SmokeRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $TempBase ("NEBUMTK_BUILD_SMOKE_" + [Guid]::NewGuid().ToString("N")))
    )
    $TempPrefix = $TempBase.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $SmokeRoot.StartsWith($TempPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-Host "Refusing unsafe packaged smoke target: $SmokeRoot" -ForegroundColor Red
        exit 1
    }
    Copy-Item -LiteralPath $DistributionRoot -Destination $SmokeRoot -Recurse -Force
    $SmokeExe = Join-Path $SmokeRoot "NEBUMTK_UNLOCK_TOOLS.exe"
    if (-not (Test-Path -LiteralPath $SmokeExe -PathType Leaf)) {
        Write-Host "Packaged smoke copy is missing the executable: $SmokeExe" -ForegroundColor Red
        Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
        exit 1
    }
    Write-Host "Packaged launch smoke: starting isolated copy" -ForegroundColor Yellow
    $Process = Start-Process -FilePath $SmokeExe -WorkingDirectory $SmokeRoot -PassThru -WindowStyle Hidden
    try {
        $Exited = $Process.WaitForExit($SmokeTimeoutSeconds * 1000)
        if ($Exited) {
            Write-Host "packaged launch smoke failed: process exited with code $($Process.ExitCode)" -ForegroundColor Red
            exit 1
        }
        Write-Host "Packaged launch smoke passed: process stayed alive for $SmokeTimeoutSeconds seconds (pid $($Process.Id))" -ForegroundColor Green
    } finally {
        if (-not $Process.HasExited) {
            & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
            if (-not $Process.HasExited) {
                Stop-Process -Id $Process.Id -Force
            }
            $Process.WaitForExit(5000) | Out-Null
        }
        if (Test-Path -LiteralPath $SmokeRoot) {
            Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
        }
    }

    python tools/audit_distribution.py $DistributionRoot
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Distribution changed during isolated launch smoke" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Write-Host ""
Write-Host "Done:" -ForegroundColor Green
Write-Host $ExePath
if ($OneFile) {
    Write-Host "Note: workspace/ is created next to this single exe on first run." -ForegroundColor Yellow
} else {
    Write-Host "Copy the whole .dist folder when distributing." -ForegroundColor Yellow
}
