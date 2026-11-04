# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Place the pinned SakiSU upstream host patcher in the local material root.

SakiSU is a separate project (https://github.com/XingChenRS/SakiSU) and this
repository does not redistribute it. This tool is the index between the two: it
copies (or downloads) the pinned ``v4.3.0-sakisu.1`` host patcher into
``material/sakisu/``, verifies size/SHA-256/PE identity, and reports the store
the loader will use.

    python tools/fetch_sakisu.py --host ksud.exe
    python tools/fetch_sakisu.py --host-url https://.../ksud.exe
    python tools/fetch_sakisu.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nebumtk_unlock_tools.core.sakisu_locations import (
    ASSET_HOST,
    ASSET_HOST_MANIFEST,
    UPSTREAM_RELEASE_URL,
    UPSTREAM_TAG,
    UPSTREAM_URL,
    sakisu_dir,
)

DEFAULT_OUTPUT = ROOT / "material" / "sakisu"
# The reviewed host patcher identity for v4.3.0-sakisu.1.
HOST_SIZE = 3_819_520
HOST_SHA256 = "1b8a6100b301700ec00b3c78f5afcab953960a30322b9f1479f283abb95805b3"
HOST_TARGET = "x86_64-pc-windows-msvc"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _verify(data: bytes) -> None:
    if len(data) != HOST_SIZE:
        raise SystemExit(
            f"host patcher size mismatch: {len(data)} != {HOST_SIZE} "
            f"(需要 SakiSU {UPSTREAM_TAG} 的 {HOST_TARGET} 版本)"
        )
    if _sha256(data) != HOST_SHA256:
        raise SystemExit(
            f"host patcher SHA-256 mismatch: {_sha256(data)} != {HOST_SHA256}"
        )
    if not data.startswith(b"MZ"):
        raise SystemExit("host patcher is not a PE image")


def _download(url: str) -> bytes:
    print(f"download {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(
            f"下载失败: {url} ({exc})；可从 {UPSTREAM_RELEASE_URL} 手工下载后用 --host 指定"
        ) from exc


def _check(store: Path) -> int:
    target = store / ASSET_HOST
    if not target.is_file():
        print(
            f"store empty: {target}\n"
            f"  SakiSU upstream: {UPSTREAM_URL} ({UPSTREAM_TAG})",
            file=sys.stderr,
        )
        return 1
    data = target.read_bytes()
    _verify(data)
    manifest = store / ASSET_HOST_MANIFEST
    print(f"host patcher ok: {target}")
    print(f"  size={len(data)} sha256={_sha256(data)}")
    print(f"  manifest={'present' if manifest.is_file() else 'absent (optional)'}")
    print(f"  upstream={UPSTREAM_URL} {UPSTREAM_TAG}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Place the pinned SakiSU {UPSTREAM_TAG} host patcher"
    )
    parser.add_argument("--host", type=Path, help="local host patcher file")
    parser.add_argument("--host-url", default="", help="download URL for the host patcher")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="verify an existing store")
    args = parser.parse_args()

    store = Path(args.output)
    print(f"SakiSU upstream: {UPSTREAM_URL} ({UPSTREAM_TAG})")
    print(f"store: {store}")

    if args.check:
        return _check(store)

    data: bytes | None = None
    if args.host is not None:
        source = Path(args.host)
        if not source.is_file():
            raise SystemExit(f"missing host patcher: {source}")
        data = source.read_bytes()
    elif args.host_url:
        data = _download(args.host_url)
    else:
        source = sakisu_dir(ROOT, needs=ASSET_HOST) / ASSET_HOST
        if source.is_file():
            print(f"already present: {source}")
            return _check(source.parent)
        raise SystemExit(
            "给出 --host/--host-url，"
            f"或从 {UPSTREAM_RELEASE_URL} 下载 {HOST_TARGET} 版本后用 --host 指定"
        )

    _verify(data)
    store.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=store, delete=False) as handle:
        handle.write(data)
        pending = Path(handle.name)
    target = store / ASSET_HOST
    pending.replace(target)
    print(f"placed {target}")
    return _check(store)


if __name__ == "__main__":
    raise SystemExit(main())
