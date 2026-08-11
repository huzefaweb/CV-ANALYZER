"""Byte-level Document intake fixtures (Story 3.2, AF-3/AF-13 gap closure).

Hand-built minimal bytes, not `python-docx`/`fpdf2` (those are worker-only
dependencies for producing readable text — this story's checks only ever
look at signatures/container/zip metadata, never extract text, so no new
heavyweight dependency is needed).
"""

from __future__ import annotations

import io
import zipfile

from pypdf import PdfWriter


def valid_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def corrupt_pdf_bytes() -> bytes:
    return b"%PDF-1.4\nthis is not a real pdf body, truncated and malformed"


def valid_docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>fixture</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )
    return buf.getvalue()


def corrupt_docx_bytes() -> bytes:
    # Starts with the real zip signature (passes signature check) but the
    # rest is truncated garbage (fails the zip-level container check).
    return b"PK\x03\x04" + b"not a real zip central directory, truncated"


def password_protected_docx_bytes() -> bytes:
    """Real Microsoft DOCX password-protection uses the OLE2/CFB "Encrypted
    Package" container, not a zip — this fixture's OLE2 magic number proves
    it is caught by the signature check (signature_mismatch), not the
    zip-level password check. See story Dev Notes."""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64


def zip_bomb_docx_bytes(uncompressed_size: int = 201 * 1024 * 1024) -> bytes:
    """A small, valid, well-formed zip (passes signature + container checks)
    whose single entry declares more uncompressed bytes than
    MAX_DOCX_UNCOMPRESSED_BYTES once actually written — proves
    check_docx_archive_expansion reads the zip's own size metadata."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"\x00" * uncompressed_size)
    return buf.getvalue()


def disguised_extension_bytes() -> bytes:
    """A DOCX-signature byte string, to be uploaded with a `.pdf` extension
    (or vice versa by the caller) — proves the extension/signature match,
    not just the signature alone, is enforced."""
    return valid_docx_bytes()


def oversized_bytes(size: int) -> bytes:
    return b"%PDF-" + (b"0" * (size - 5))
