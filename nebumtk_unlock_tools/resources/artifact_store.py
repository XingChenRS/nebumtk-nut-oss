# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Operator-owned fastboot artifact store.

The closed release carried each artifact (Vivo wipe misc, reserve LK, ...) as
AES-GCM ciphertext unlocked by the access password. This build reads them from
the local material root instead:

``<material>/artifacts/manifest.json``
    ``{"artifacts": {<id>: {"size": <int>, "sha256": "<hex>"}}}``
``<material>/artifacts/<id>.bin``
    the artifact bytes

The manifest is optional: without it an artifact is still materialized, but its
size and SHA-256 are recorded for the caller instead of being enforced. Keep the
manifest in real use — the catalog ships hashes for these files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from ..core.errors import ResourceError
from ..keymaterial import MaterialError, material_root

ARTIFACT_DIR = "artifacts"
MANIFEST_NAME = "manifest.json"


def _manifest_path(root: Path | None = None) -> Path:
    return material_root(root) / ARTIFACT_DIR / MANIFEST_NAME


def artifacts(root: Path | None = None) -> dict[str, dict[str, object]]:
    """The declared artifact inventory (empty when no manifest is present)."""
    path = _manifest_path(root)
    if not path.is_file():
        return {}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResourceError(f"artifact 清单无法解析: {path}") from exc
    records = decoded.get("artifacts", {}) if isinstance(decoded, dict) else {}
    if not isinstance(records, dict):
        raise ResourceError(f"artifact 清单结构无效: {path}")
    return {str(key): dict(value) for key, value in records.items() if isinstance(value, dict)}


def artifact_path(artifact_id: str, root: Path | None = None) -> Path:
    return material_root(root) / ARTIFACT_DIR / f"{artifact_id}.bin"


def materialize_artifact(
    artifact_id: str,
    destination: Path | None = None,
    *,
    project_root: Path | None = None,
) -> Path:
    source = artifact_path(artifact_id, project_root)
    if not source.is_file():
        raise MaterialError(
            f"缺少 fastboot artifact {artifact_id}: {source}"
            "（把文件放到 material/artifacts/ 下，或设置 NUT_MATERIAL_ROOT）"
        )
    data = source.read_bytes()
    record = artifacts(project_root).get(artifact_id, {})
    expected_sha = str(record.get("sha256", "")).strip().lower()
    if expected_sha and hashlib.sha256(data).hexdigest() != expected_sha:
        raise ResourceError(f"fastboot artifact SHA-256 校验失败: {artifact_id}")
    expected_size = record.get("size")
    if isinstance(expected_size, int) and expected_size != len(data):
        raise ResourceError(f"fastboot artifact 大小校验失败: {artifact_id}")

    if destination is None:
        import tempfile

        destination = Path(
            tempfile.mkdtemp(prefix="nebumtk-fastboot-artifact-")
        ) / "artifact.bin"
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    return destination


def import_artifact(source: Path, artifact_id: str, root: Path | None = None) -> Path:
    """Copy one local artifact into the store and refresh the manifest."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"missing artifact source: {source}")
    data = source.read_bytes()
    target_dir = material_root(root) / ARTIFACT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{artifact_id}.bin"
    shutil.copyfile(source, target)
    records = artifacts(root)
    records[artifact_id] = {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    _manifest_path(root).write_text(
        json.dumps({"version": 1, "artifacts": records}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
