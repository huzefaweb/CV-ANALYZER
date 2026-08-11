"""Document intake validation (FR-5, FR-6, NFR-8, AF-3) — pure functions,
no framework/DB/filesystem/parser-SDK import (AR-4). Byte-signature and
zip-metadata detection that needs a real library import lives in the
adapter layer (`adapters/document_detection.py`), mirroring how the worker
splits `domain/parse_gates.py` (pure) from `adapters/pdf_parser.py`/
`docx_parser.py` (SDK-importing).
"""

from __future__ import annotations

from enum import Enum

MAX_DOCUMENT_COUNT = 20

# 10 MB, matching FR-5's literal "at most 10 MB each".
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

ALLOWED_EXTENSIONS = frozenset({".pdf", ".docx"})

PDF_SIGNATURE = b"%PDF-"
# OOXML/zip local-file-header magic. A genuinely password-protected .docx
# uses the OLE2/CFB "Encrypted Package" container instead — it never starts
# with this signature, so it is caught here (signature_mismatch), not by the
# zip-level password/container check downstream. See story Dev Notes.
DOCX_SIGNATURE = b"PK\x03\x04"

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# 20x the compressed 10 MB cap — a deliberately simple, pinned V1 ceiling for
# the archive-expansion (zip-bomb) gate. Not derived from a production
# tuning exercise; documented here as a reasonable, explicit constant.
MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


class RejectionCategory(str, Enum):
    """Reuses corpus-manifest.md's frozen rejection-category vocabulary
    verbatim for the four categories it already named (count_exceeded and
    archive_expansion are new, closing the two AF-13-disclosed gaps)."""

    COUNT_EXCEEDED = "count_exceeded"
    SIZE_LIMIT = "size_limit"
    EXTENSION_REJECTED = "extension_rejected"
    SIGNATURE_MISMATCH = "signature_mismatch"
    PASSWORD_PROTECTED = "password_protected"
    CORRUPT_CONTAINER = "corrupt_container"
    ARCHIVE_EXPANSION = "archive_expansion"
    CONFLICT = "conflict"
    INVALID_REQUEST = "invalid_request"


# DB column widths (models.py) — validated here so an oversized value is a
# clean sanitized rejection, not an uncaught database DataError/500.
MAX_FILENAME_LENGTH = 255
MAX_IDEMPOTENCY_KEY_LENGTH = 128


def check_extension(filename: str) -> bool:
    """Case-insensitive suffix check against ALLOWED_EXTENSIONS."""
    lowered = filename.lower()
    return any(lowered.endswith(ext) for ext in ALLOWED_EXTENSIONS)


def detect_signature(head: bytes) -> str | None:
    """Returns the detected content type from the first bytes of a file, or
    None if unrecognized. Pure byte comparison — no parser SDK."""
    if head.startswith(PDF_SIGNATURE):
        return PDF_CONTENT_TYPE
    if head.startswith(DOCX_SIGNATURE):
        return DOCX_CONTENT_TYPE
    return None
