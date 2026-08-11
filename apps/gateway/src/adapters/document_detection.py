"""Byte-level Document detection (FR-6, AF-3) needing a real library import
(`pypdf`, `zipfile`) — split out of `domain/document_intake.py` so that
module stays pure (AR-4), mirroring the worker's `parse_gates.py`
(pure) vs. `pdf_parser.py`/`docx_parser.py` (adapter) split.

Mirrors `apps/worker/src/adapters/pdf_parser.py`'s pinned pypdf>=6.15.0
password/corrupt-container detection exactly (same reader.is_encrypted /
decrypt("") / PdfReadError logic) rather than re-deriving new detection
logic — the gateway cannot import the worker's module (separate app,
separate dependency set), so this is deliberate, minimal duplication of an
already fixture-proven check, not a shared-library gap.
"""

from __future__ import annotations

import io
import zipfile
from typing import Literal

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..domain.document_intake import MAX_DOCX_UNCOMPRESSED_BYTES

ContainerResult = Literal["ok", "password_protected", "corrupt_container"]


def check_pdf_password_and_container(data: bytes) -> ContainerResult:
    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError:
        return "corrupt_container"

    if reader.is_encrypted:
        try:
            result = reader.decrypt("")
        except Exception:  # noqa: BLE001 - any decrypt failure is fatal
            return "password_protected"
        if result == 0:
            return "password_protected"

    # Confirm the container is actually readable beyond the header (a
    # truncated/corrupt PDF can pass PdfReader's constructor but fail here).
    try:
        _ = len(reader.pages)
    except Exception:  # noqa: BLE001 - any pypdf failure here is fatal
        return "corrupt_container"

    return "ok"


def check_docx_password_and_container(data: bytes) -> ContainerResult:
    """A DOCX that reaches this function already passed the zip-signature
    check upstream, so real Microsoft password-protection (OLE2/CFB, not a
    valid zip) never reaches here — see story Dev Notes. This is the
    fixture-verified backstop AF-13 flagged as previously unverified: a
    corrupt/truncated zip container is still caught here."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if zf.testzip() is not None:
                return "corrupt_container"
    except zipfile.BadZipFile:
        return "corrupt_container"
    return "ok"


def check_docx_archive_expansion(data: bytes) -> bool:
    """True if within limits. Assumes `data` already passed the container
    check above (a well-formed zip)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        total_uncompressed = sum(info.file_size for info in zf.infolist())
    return total_uncompressed <= MAX_DOCX_UNCOMPRESSED_BYTES
