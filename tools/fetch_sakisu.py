# SPDX-License-Identifier: AGPL-3.0-only
"""Fetch and import the reviewed SakiSU upstream assets.

SakiSU is a separate project (https://github.com/XingChenRS/SakiSU), not part of
this repository. This tool is the index between them: it takes the pinned
release (local file or download), verifies the pinned identity through the
project's own importers, and writes the result into ``material/sakisu/`` where
the loaders look for it.

    python tools/fetch_sakisu.py --download            # both assets from upstream
    python tools/fetch_sakisu.py --apk SakiSU.apk --host ksud.exe
    python tools/fetch_sakisu.py --check               # verify an existing store

Nothing is written outside ``material/sakisu`` (or ``--output``).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nebumtk_unlock_tools.sakisu import locations
from nebumtk_unlock_tools.sakisu.host_importer import (
    OFFICIAL_WINDOWS_HOST,
    import_reviewed_host_patcher,
    load_imported_host_patcher,
)
from nebumtk_unlock_tools.sakisu.importer import (
    OFFICIAL_APK,
    import_official_apk,
    load_imported_patcher,
)

DEFAULT_OUTPUT = ROOT / "material" / "sakisu"
APK_URL = (
    f"https://github.com/{locations.UPSTREAM_REPOSITORY}/releases/download/"
    f"{locations.UPSTREAM_TAG}/{OFFICIAL_APK.asset_name}"
)


def _download(url: str, destination: Path) -> Path:
    print(f"download {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"下载失败: {url} ({exc})") from exc
    destination.write_bytes(payload)
    return destination


def _import_apk(apk: Path, output: Path) -> dict[str, object]:
    return import_official_apk(apk, output)


def _import_host(exe: Path, output: Path) -> dict[str, object]:
    return import_reviewed_host_patcher(exe, output)


def _check(output: Path) -> None:
    os.environ[locations.SAKISU_HOME_ENV] = str(output)
    snapshot, manifest = load_imported_patcher(ROOT)
    upstream = manifest.get("upstream", {})
    print(
        f"arm64 patcher ok: {snapshot.path} "
        f"size={snapshot.size} sha256={snapshot.sha256}"
    )
    print(f"  upstream: {upstream.get('repository')} {upstream.get('tag')}")
    host_snapshot, host_manifest = load_imported_host_patcher(ROOT)
    host_upstream = host_manifest.get("upstream", {})
    print(
        f"windows host ok: {host_snapshot.path} "
        f"size={host_snapshot.size} sha256={host_snapshot.sha256}"
    )
    print(f"  upstream: {host_upstream.get('repository')} {host_upstream.get('tag')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Import the pinned SakiSU {locations.UPSTREAM_TAG} assets"
    )
    parser.add_argument("--apk", type=Path, help="local official SakiSU APK")
    parser.add_argument("--host", type=Path, help="local reviewed Windows host patcher")
    parser.add_argument("--download", action="store_true", help="download the APK from upstream")
    parser.add_argument(
        "--host-url",
        default="",
        help="download the Windows host patcher from this URL",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="verify an existing store")
    args = parser.parse_args()

    output = Path(args.output)
    print(f"SakiSU upstream: {locations.UPSTREAM_URL} ({locations.UPSTREAM_TAG})")
    print(f"store: {output}")

    if args.check:
        _check(output)
        return

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sakisu-fetch-") as raw:
        scratch = Path(raw)

        apk = Path(args.apk) if args.apk else None
        if apk is None and args.download:
            apk = _download(APK_URL, scratch / OFFICIAL_APK.asset_name)
        if apk is not None:
            if not apk.is_file():
                raise SystemExit(f"missing APK: {apk}")
            manifest = _import_apk(apk, output)
            print(f"imported arm64 patcher from {manifest['import_source']}")

        host = Path(args.host) if args.host else None
        if host is None and args.host_url:
            host = _download(args.host_url, scratch / locations.ASSET_HOST)
        if host is not None:
            if not host.is_file():
                raise SystemExit(f"missing host patcher: {host}")
            host_manifest = _import_host(host, output)
            print(f"imported Windows host patcher ({host_manifest['patcher']['target']})")

    if not (output / locations.ASSET_ARM64).is_file() and not (
        output / locations.ASSET_HOST
    ).is_file():
        print(
            "没有导入任何产物。给出 --apk/--download 与 --host/--host-url，"
            f"或手工把 {locations.UPSTREAM_TAG} 的产物放到 {output}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    _check(output)


if __name__ == "__main__":
    main()
