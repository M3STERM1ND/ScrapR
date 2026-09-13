"""Extraction and chunking (`REQ-DOC-001`, `REQ-DOC-004`, `DEC-14`).

Real files, built in memory by the same libraries that will read them back. A
test against a hand-written byte string proves the parser accepts that byte
string; it says nothing about what a document produced by Word looks like.
"""

from __future__ import annotations

import csv
import io
import zipfile

import pytest
from openpyxl import Workbook
from pypdf import PdfWriter

from scrapr_core.storage.extract import (
    ACCEPTED_TYPES,
    CHUNK_CHARS,
    ExtractionError,
    accepted_types_message,
    describe_locator,
    detect_content_type,
    extract_chunks,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def _pdf(pages: list[str]) -> bytes:
    """A PDF with real text on each page.

    `pypdf` cannot author text, so the pages are drawn as minimal content
    streams. Verbose, and worth it: the alternative is asserting extraction
    against a fixture nobody can read or change.
    """
    writer = PdfWriter()
    for text in pages:
        writer.add_blank_page(width=612, height=792)
        page = writer.pages[-1]
        stream = (
            f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
        )
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        content = DecodedStreamObject()
        content.set_data(stream)
        page[NameObject("/Contents")] = writer._add_object(content)
        font = DictionaryObject()
        font.update(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        resources = DictionaryObject()
        fonts = DictionaryObject()
        fonts[NameObject("/F1")] = writer._add_object(font)
        resources[NameObject("/Font")] = fonts
        page[NameObject("/Resources")] = resources

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    import docx

    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if table:
        added = document.add_table(rows=len(table), cols=len(table[0]))
        for row_index, row in enumerate(table):
            for column_index, value in enumerate(row):
                added.cell(row_index, column_index).text = value

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _xlsx(rows: list[list[object]], title: str = "Sheet1") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = title
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode()


# --------------------------------------------------------------------------
# Type detection — `DEC-14`: bytes decide, not the filename
# --------------------------------------------------------------------------


def test_pdf_is_detected_from_its_signature() -> None:
    assert detect_content_type(_pdf(["hello"]), "anything.txt") == "application/pdf"


def test_docx_and_xlsx_are_told_apart_inside_the_zip() -> None:
    """Both are zip archives. The difference is which directory is inside."""
    assert detect_content_type(_docx(["hello"]), "report.docx") == DOCX
    assert detect_content_type(_xlsx([["a", 1]]), "numbers.xlsx") == XLSX


def test_a_renamed_executable_is_not_accepted() -> None:
    """The extension is a claim; the bytes are the answer.

    This is the case the allowlist at the API boundary cannot catch: the client
    declared `text/plain` and the presign check passed, because at that point
    nobody had seen the file.
    """
    assert detect_content_type(b"MZ\x90\x00\x03\x00\x00\x00", "notes.txt") is None


def test_a_zip_that_is_neither_office_format_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("photos/cat.jpg", b"\xff\xd8\xff")
    assert detect_content_type(buffer.getvalue(), "album.zip") is None


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("notes.txt", "text/plain"),
        ("README.md", "text/markdown"),
        ("data.csv", "text/csv"),
        ("no-extension", "text/plain"),
    ],
)
def test_text_formats_are_separated_by_extension(filename: str, expected: str) -> None:
    """Text has no signature, so the name picks between the text formats.

    That is all the filename is trusted for, and the consequence of getting it
    wrong is a different chunking strategy — not a different parser.
    """
    assert detect_content_type(b"a,b,c\n1,2,3\n", filename) == expected


def test_every_detected_type_is_an_accepted_one() -> None:
    """Detection and the allowlist cannot disagree.

    If they did, a file could be detected as a type with no extractor, and
    `extract_chunks` would raise on a document the API had already accepted.
    """
    for data, name in (
        (_pdf(["x"]), "a.pdf"),
        (_docx(["x"]), "a.docx"),
        (_xlsx([["x"]]), "a.xlsx"),
        (b"a,b\n1,2\n", "a.csv"),
        (b"hello", "a.txt"),
        (b"# hello", "a.md"),
    ):
        detected = detect_content_type(data, name)
        assert detected in ACCEPTED_TYPES


# --------------------------------------------------------------------------
# Extraction, per format
# --------------------------------------------------------------------------


def test_pdf_chunks_carry_a_page_locator() -> None:
    """`REQ-DOC-006`: a citation resolves to a page, not to "the PDF"."""
    chunks = extract_chunks(_pdf(["Revenue was 1.2bn"]), "application/pdf")
    assert chunks
    assert chunks[0].locator == {"page": 1}
    assert "1.2bn" in chunks[0].text


def test_docx_tables_are_extracted_not_skipped() -> None:
    """The numbers in a report live in its tables.

    Extracting only paragraphs would drop exactly the evidence a financial
    document was uploaded for, and the document would look empty of figures
    while appearing to have been read successfully.
    """
    data = _docx(
        ["Summary of the year."],
        table=[["Metric", "Value"], ["Revenue", "$1.2bn"]],
    )
    text = " ".join(chunk.text for chunk in extract_chunks(data, DOCX))
    assert "Summary of the year." in text
    assert "$1.2bn" in text


def test_xlsx_rows_carry_the_sheet_name() -> None:
    data = _xlsx([["Metric", "Value"], ["Revenue", 1200000]], title="FY2025")
    chunks = extract_chunks(data, XLSX)
    assert chunks
    assert chunks[0].locator["sheet"] == "FY2025"
    assert "1200000" in chunks[0].text


def test_csv_rows_are_joined_readably() -> None:
    chunks = extract_chunks(_csv([["Metric", "Value"], ["Revenue", "1.2bn"]]), "text/csv")
    assert "Metric | Value" in chunks[0].text


def test_markdown_and_plain_text_split_on_blank_lines() -> None:
    data = b"First block.\n\n\nSecond block.\n"
    chunks = extract_chunks(data, "text/plain")
    text = " ".join(chunk.text for chunk in chunks)
    assert "First block." in text
    assert "Second block." in text


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def test_chunks_are_bounded_and_ordinals_are_contiguous() -> None:
    """A chunk is a passage, and ordinals address them in order.

    The unique index is on `(upload_id, ordinal)`, so a gap or a repeat is a
    write that fails at insert rather than a chunk that goes missing quietly.
    """
    paragraph = ("word " * 60).strip()
    data = ("\n\n".join([paragraph] * 40)).encode()

    chunks = extract_chunks(data, "text/plain")

    assert len(chunks) > 1
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    # The accumulator flushes *at* the limit, so the last piece added can carry
    # a chunk past it. Bounded, not capped, and the slack is one piece.
    assert all(len(chunk.text) < CHUNK_CHARS * 2 for chunk in chunks)


def test_a_chunk_is_addressed_by_where_it_starts() -> None:
    """A chunk spanning pages 3 and 4 cites page 3.

    Where a reader should begin looking. Precise enough to find, and honest
    about being approximate.
    """
    chunks = extract_chunks(_pdf(["a" * 900, "b" * 900, "c" * 900]), "application/pdf")
    assert chunks[0].locator == {"page": 1}


def test_blank_content_produces_no_chunks_rather_than_an_empty_one() -> None:
    """`REQ-DOC-004 AC-3` needs the caller to be able to tell.

    An empty list means "nothing in it"; an exception means "could not read
    it". Returning one empty chunk would be a third thing that looks like a
    document with a blank page in it.
    """
    assert extract_chunks(b"   \n\n  \n", "text/plain") == []


# --------------------------------------------------------------------------
# Failure
# --------------------------------------------------------------------------


def test_a_corrupt_pdf_raises_rather_than_returning_nothing() -> None:
    with pytest.raises(ExtractionError, match="PDF"):
        extract_chunks(b"%PDF-1.7\nnot actually a pdf", "application/pdf")


def test_a_corrupt_docx_raises() -> None:
    with pytest.raises(ExtractionError, match="Word"):
        extract_chunks(b"PK\x03\x04word/broken", DOCX)


def test_non_utf8_text_raises_rather_than_mangling() -> None:
    """Better to say the file could not be read than to store mojibake.

    Silently decoding with `errors="replace"` would produce chunks full of
    replacement characters, mark the upload ready, and leave the user with a
    document that was accepted and says nothing.
    """
    with pytest.raises(ExtractionError):
        extract_chunks(b"\xff\xfe\x00\x01 not utf-8", "text/plain")


def test_an_unknown_type_raises_rather_than_guessing() -> None:
    with pytest.raises(ExtractionError, match="no extractor"):
        extract_chunks(b"anything", "application/x-unknown")


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("locator", "expected"),
    [
        ({"page": 14}, "page 14"),
        ({"sheet": "FY2025", "row": 3}, "sheet FY2025, row 3"),
        ({"table": 2, "row": 7}, "table 2, row 7"),
        ({"paragraph": 9}, "paragraph 9"),
        ({"row": 4}, "row 4"),
        ({"line": 22}, "line 22"),
        ({}, "the document"),
    ],
)
def test_locators_read_as_words(locator: dict[str, object], expected: str) -> None:
    """`REQ-DOC-008 AC-2`: what the reader sees in the citation."""
    assert describe_locator(locator) == expected


def test_the_rejection_message_lists_what_is_accepted() -> None:
    """`REQ-DOC-001 AC-2`. Generated from the allowlist, so the message and the
    list cannot disagree the day one of them changes."""
    message = accepted_types_message()
    for label in set(ACCEPTED_TYPES.values()):
        assert label in message
