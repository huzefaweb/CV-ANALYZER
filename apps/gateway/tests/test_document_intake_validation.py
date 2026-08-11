"""Pure-function boundary tests for src/domain/document_intake.py and
src/adapters/document_detection.py (no DB, no DATABASE_URL skip needed)."""

from __future__ import annotations

from src.adapters.document_detection import (
    check_docx_archive_expansion,
    check_docx_password_and_container,
    check_pdf_password_and_container,
)
from src.domain.document_intake import (
    DOCX_CONTENT_TYPE,
    MAX_DOCX_UNCOMPRESSED_BYTES,
    PDF_CONTENT_TYPE,
    check_extension,
    detect_signature,
)
from tests.fixtures.document_fixtures import (
    corrupt_docx_bytes,
    corrupt_pdf_bytes,
    password_protected_docx_bytes,
    valid_docx_bytes,
    valid_pdf_bytes,
    zip_bomb_docx_bytes,
)


def test_check_extension_accepts_pdf_and_docx_case_insensitively():
    assert check_extension("resume.pdf")
    assert check_extension("Resume.PDF")
    assert check_extension("resume.docx")
    assert check_extension("Resume.DOCX")


def test_check_extension_rejects_everything_else():
    assert not check_extension("resume.doc")
    assert not check_extension("resume.exe")
    assert not check_extension("resume")


def test_detect_signature_recognizes_pdf():
    assert detect_signature(valid_pdf_bytes()[:8]) == PDF_CONTENT_TYPE


def test_detect_signature_recognizes_docx():
    assert detect_signature(valid_docx_bytes()[:8]) == DOCX_CONTENT_TYPE


def test_detect_signature_returns_none_for_garbage():
    assert detect_signature(b"not a real file header") is None


def test_detect_signature_returns_none_for_ole2_password_protected_docx():
    # The concrete AF-13 finding: real DOCX password-protection uses OLE2,
    # not zip — signature detection fails before any zip-level check runs.
    assert detect_signature(password_protected_docx_bytes()[:8]) is None


def test_check_pdf_password_and_container_accepts_valid_pdf():
    assert check_pdf_password_and_container(valid_pdf_bytes()) == "ok"


def test_check_pdf_password_and_container_rejects_corrupt_pdf():
    assert check_pdf_password_and_container(corrupt_pdf_bytes()) == "corrupt_container"


def test_check_docx_password_and_container_accepts_valid_docx():
    assert check_docx_password_and_container(valid_docx_bytes()) == "ok"


def test_check_docx_password_and_container_rejects_corrupt_docx():
    assert check_docx_password_and_container(corrupt_docx_bytes()) == "corrupt_container"


def test_check_docx_archive_expansion_accepts_valid_docx():
    assert check_docx_archive_expansion(valid_docx_bytes()) is True


def test_check_docx_archive_expansion_accepts_just_under_the_boundary():
    data = zip_bomb_docx_bytes(uncompressed_size=MAX_DOCX_UNCOMPRESSED_BYTES - 1000)
    assert check_docx_archive_expansion(data) is True


def test_check_docx_archive_expansion_rejects_just_over_the_boundary():
    data = zip_bomb_docx_bytes(uncompressed_size=MAX_DOCX_UNCOMPRESSED_BYTES + 1000)
    assert check_docx_archive_expansion(data) is False
