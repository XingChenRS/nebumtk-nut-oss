#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WINEPREFIX="${WINEPREFIX:-$HOME/.cache/mtkbl-wineprefix}"
WINEDEBUG="${WINEDEBUG:--all}"
WINE_USER="${WINE_USER:-$USER}"
PYTHON_UNIX="${PYTHON_UNIX:-$WINEPREFIX/drive_c/users/$WINE_USER/AppData/Local/Programs/Python/Python312/python.exe}"
ICU_RELEASE_URL="${ICU_RELEASE_URL:-https://github.com/FaithLife-Community/icu/releases/download/72.1-custom%2B4/icu-win.tar.gz}"
OUTPUT_DIR="${OUTPUT_DIR:-dist-win}"
HOST_PYTHON="${HOST_PYTHON:-python3}"

SKIP_INSTALL=0
SKIP_ICU=0
ONEFILE=0

usage() {
    cat <<'EOF'
Usage: scripts/build-wine.sh [--skip-install] [--skip-icu] [--onefile]

Build the Windows package from Linux via Wine.

Options:
  --skip-install  Do not run pip install inside Wine.
  --skip-icu      Do not auto-install ICU DLLs into the Wine prefix.
  --onefile       Build a onefile exe instead of standalone.
EOF
}

while (($#)); do
    case "$1" in
        --skip-install)
            SKIP_INSTALL=1
            ;;
        --skip-icu)
            SKIP_ICU=1
            ;;
        --onefile)
            ONEFILE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

if [[ ! -f "$PYTHON_UNIX" ]]; then
    echo "Windows Python not found: $PYTHON_UNIX" >&2
    echo "Install Python 3.12 into the Wine prefix first, or override PYTHON_UNIX." >&2
    exit 1
fi

"$HOST_PYTHON" tools/build_device_catalog.py
if [[ -f "material/payloads/manifest.json" ]]; then
    "$HOST_PYTHON" tools/embed_preloads.py
else
    echo "No local preload store; building without device payload material"
fi

run_wine_python() {
    WINEPREFIX="$WINEPREFIX" WINEDEBUG="$WINEDEBUG" wine "$PYTHON_UNIX" "$@"
}

ensure_wine_icu() {
    local tmpdir
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' RETURN

    curl -fL -o "$tmpdir/icu-win.tar.gz" "$ICU_RELEASE_URL"
    tar xzf "$tmpdir/icu-win.tar.gz" -C "$tmpdir"

    mkdir -p \
        "$WINEPREFIX/drive_c/windows/system32" \
        "$WINEPREFIX/drive_c/windows/syswow64" \
        "$WINEPREFIX/drive_c/windows/globalization/ICU"

    cp \
        "$tmpdir/windows/system32/icu.dll" \
        "$tmpdir/windows/system32/icuin.dll" \
        "$tmpdir/windows/system32/icuuc.dll" \
        "$WINEPREFIX/drive_c/windows/system32/"
    cp \
        "$tmpdir/windows/syswow64/icu.dll" \
        "$tmpdir/windows/syswow64/icuin.dll" \
        "$tmpdir/windows/syswow64/icuuc.dll" \
        "$WINEPREFIX/drive_c/windows/syswow64/"
    cp \
        "$tmpdir/windows/globalization/ICU/icudtl.dat" \
        "$WINEPREFIX/drive_c/windows/globalization/ICU/"

    WINEPREFIX="$WINEPREFIX" WINEDEBUG="$WINEDEBUG" wine reg add "HKCU\\Software\\Wine\\DllOverrides" /v icu /t REG_SZ /d native /f >/dev/null
    WINEPREFIX="$WINEPREFIX" WINEDEBUG="$WINEDEBUG" wine reg add "HKCU\\Software\\Wine\\DllOverrides" /v icuin /t REG_SZ /d native /f >/dev/null
    WINEPREFIX="$WINEPREFIX" WINEDEBUG="$WINEDEBUG" wine reg add "HKCU\\Software\\Wine\\DllOverrides" /v icuuc /t REG_SZ /d native /f >/dev/null
}

verify_qt_runtime() {
    run_wine_python -c "import ordered_set, zstandard, yaml, PySide6.QtCore; print('qt-runtime-ok')"
}

if (( SKIP_INSTALL == 0 )); then
    run_wine_python -m pip install -r requirements.txt
    run_wine_python -m pip install nuitka ordered-set zstandard
fi

if (( SKIP_ICU == 0 )); then
    if [[ ! -f "$WINEPREFIX/drive_c/windows/system32/icuuc.dll" ]]; then
        ensure_wine_icu
    fi
fi

verify_qt_runtime

ROOT_WIN="$(WINEPREFIX="$WINEPREFIX" winepath -w "$ROOT")"

BUILD_ARGS=(
    "--standalone"
    "--mingw64"
    "--lto=no"
    "--experimental=force-dependencies-pefile"
    "--windows-console-mode=disable"
    "--enable-plugin=pyside6"
    "--include-data-dir=misc=misc"
    "--output-dir=$OUTPUT_DIR"
    "--output-filename=NEBUMTK_UNLOCK_TOOLS.exe"
    "--remove-output"
    "--assume-yes-for-downloads"
    "--python-flag=-m"
    "--python-flag=no_docstrings"
    "--python-flag=isolated"
    "--python-flag=safe_path"
)

if (( ONEFILE == 1 )); then
    BUILD_ARGS+=("--onefile")
fi

BUILD_ARGS+=("nebumtk_unlock_tools")

WIN_CMD="cd /d $ROOT_WIN && python -m nuitka"
for arg in "${BUILD_ARGS[@]}"; do
    WIN_CMD+=" $arg"
done

WINEPREFIX="$WINEPREFIX" WINEDEBUG="$WINEDEBUG" wine cmd /c "$WIN_CMD"

if (( ONEFILE == 1 )); then
    EXE_PATH="$OUTPUT_DIR/NEBUMTK_UNLOCK_TOOLS.exe"
else
    EXE_PATH="$OUTPUT_DIR/nebumtk_unlock_tools.dist/NEBUMTK_UNLOCK_TOOLS.exe"
fi

if [[ ! -f platform-tools/adb.exe || ! -f platform-tools/fastboot.exe ]]; then
    echo "platform-tools source is missing adb.exe or fastboot.exe" >&2
    exit 1
fi
PLATFORM_TOOLS_TARGET="$(dirname "$EXE_PATH")/platform-tools"
rm -rf "$PLATFORM_TOOLS_TARGET"
cp -a platform-tools "$PLATFORM_TOOLS_TARGET"
if [[ ! -f "$PLATFORM_TOOLS_TARGET/adb.exe" || ! -f "$PLATFORM_TOOLS_TARGET/fastboot.exe" ]]; then
    echo "Failed to copy platform-tools next to built exe" >&2
    exit 1
fi

SAKISU_TARGET="$(dirname "$EXE_PATH")/sakisu"
if [[ ! -f sakisu/official-patcher-windows-x64.exe ]]; then
    echo "sakisu source is missing the reviewed Windows host patcher" >&2
    exit 1
fi
rm -rf "$SAKISU_TARGET"
cp -a sakisu "$SAKISU_TARGET"
if [[ ! -f "$SAKISU_TARGET/official-patcher-windows-x64.exe" ]]; then
    echo "Failed to copy the SakiSU host patcher next to built exe" >&2
    exit 1
fi

LIBUSB_PACKAGE_WIN="$(run_wine_python -c "import libusb; print(libusb.__file__)")"
LIBUSB_PACKAGE_UNIX="$(WINEPREFIX="$WINEPREFIX" winepath -u "$LIBUSB_PACKAGE_WIN")"
LIBUSB_SOURCE="$(dirname "$LIBUSB_PACKAGE_UNIX")/_platform/_windows/x64/libusb-1.0.dll"
LIBUSB_TARGET="$(dirname "$EXE_PATH")/nebumtk_unlock_tools/lk/libusb/_platform/_windows/x64/libusb-1.0.dll"
if [[ ! -f "$LIBUSB_SOURCE" ]]; then
    echo "libusb-1.0.dll source not found: $LIBUSB_SOURCE" >&2
    exit 1
fi
mkdir -p "$(dirname "$LIBUSB_TARGET")"
cp -f "$LIBUSB_SOURCE" "$LIBUSB_TARGET"

"$HOST_PYTHON" tools/audit_distribution.py --write-manifest "$(dirname "$EXE_PATH")"

echo "$EXE_PATH"
