"""Restricted local source-byte volume writer (AR-2, AR-36).

`/restricted-source` is docker-compose.yml's existing `restricted-source-
volume` mount on the gateway/worker services (provisioned since Story
1.1) — this module is its first real writer. `DOCUMENT_STORAGE_ROOT` is
overridable via env var because gateway tests run directly on the host
(no Docker), where `/restricted-source` does not exist.

Write-only by design (AR-36: "V1 exposes no original Resume viewer/
download") — no read/serve function is added; the worker reads files
directly off the shared volume by path in a later epic, which is that
story's concern, not this one's.
"""

from __future__ import annotations

import os

DOCUMENT_STORAGE_ROOT = os.environ.get("DOCUMENT_STORAGE_ROOT", "/restricted-source")


def store(document_id: str, content_version: int, data: bytes) -> str:
    """Writes `data` to an opaque path derived only from `document_id`/
    `content_version` (never the original filename, per AR-36) and returns
    the stored path."""
    directory = os.path.join(DOCUMENT_STORAGE_ROOT, document_id)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"v{content_version}")
    with open(path, "wb") as f:
        f.write(data)
    return path
