"""Route A sur un vrai PDF natif, créé en temporaire et jamais téléchargé."""

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from scripts.make_demo_pdf import make_demo_pdf

from rulelawyer.ingest import ingest_route_a, read_chunks, write_chunks
from rulelawyer.models import PageMapReport, Route
from rulelawyer.probe import probe


def test_outline_sections_preserve_printed_pages_and_embedded_path(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    report = probe(pdf)
    assert report.recommended_route is Route.A
    assert report.page_count == 4
    chunks = ingest_route_a(pdf, report)
    assert len(chunks) == 4
    bridge = next(c for c in chunks if c.section_path.endswith("Pont de brume"))
    assert bridge.section_path == "Manuel des Veilleurs > Pont de brume"
    assert bridge.text.startswith(bridge.section_path + "\n\n")
    assert "3 étincelles" in bridge.raw_text
    assert "7 minutes" not in bridge.raw_text
    assert bridge.book_page == 42
    assert bridge.pdf_page == 2
    assert [p.book_page for p in bridge.pages] == [42]
    assert "\n42" not in bridge.raw_text
    output = tmp_path / "chunks.jsonl"
    write_chunks(chunks, output)
    assert read_chunks(output) == chunks


def test_section_spans_pages_without_citing_the_first_page_for_everything(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.pdf"
    make_demo_pdf(original)
    writer = PdfWriter()
    for page in PdfReader(original).pages:
        writer.add_page(page)
    root = writer.add_outline_item("Manuel des Veilleurs", 0)
    writer.add_outline_item("Présentation", 0, parent=root)
    writer.add_outline_item("Pont de brume", 1, parent=root)
    writer.add_outline_item("Lexique", 3, parent=root)
    pdf = tmp_path / "multipage.pdf"
    writer.write(pdf)
    report = probe(pdf)
    assert report.recommended_route is Route.A
    chunks = ingest_route_a(pdf, report)
    bridge = next(c for c in chunks if c.section_path.endswith("Pont de brume"))
    assert [p.book_page for p in bridge.pages] == [42, 43]
    assert "7 minutes" in bridge.pages[1].text
    assert "7 minutes" not in bridge.pages[0].text


def test_ingestion_obeys_probe_and_never_promotes_pdf_fallback_to_printed_page(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    report = probe(pdf)
    report.page_map = PageMapReport()
    assert all(c.book_page is None for c in ingest_route_a(pdf, report))
    report.recommended_route = Route.B
    with pytest.raises(ValueError, match="Route B"):
        ingest_route_a(pdf, report)


def test_out_of_order_bookmarks_keep_hierarchy_but_follow_pdf_pages(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.pdf"
    make_demo_pdf(original)
    writer = PdfWriter()
    for page in PdfReader(original).pages:
        writer.add_page(page)
    root = writer.add_outline_item("Manuel des Veilleurs", 0)
    for title, index in [
        ("Lexique", 3),
        ("Pont de brume", 1),
        ("Repos de cristal", 2),
        ("Présentation", 0),
    ]:
        writer.add_outline_item(title, index, parent=root)
    pdf = tmp_path / "reordered.pdf"
    writer.write(pdf)
    chunks = ingest_route_a(pdf, probe(pdf))
    assert [c.book_page for c in chunks] == [41, 42, 43, 44]
    assert all(c.section_path.startswith("Manuel des Veilleurs > ") for c in chunks)
    assert "3 étincelles" in chunks[1].raw_text
    assert "7 minutes" not in chunks[1].raw_text


def test_two_columns_do_not_merge_sibling_rules(tmp_path: Path) -> None:
    from reportlab.pdfgen.canvas import Canvas

    pdf = tmp_path / "columns.pdf"
    canvas = Canvas(str(pdf), pagesize=(595, 842))
    canvas.bookmarkPage("manual")
    canvas.addOutlineEntry("Manual", "manual", level=0)
    for title, key, x, text in [
        ("LEFT RULE", "left", 40, "Left rule costs three sparks."),
        ("RIGHT RULE", "right", 315, "Right rule restores two sparks."),
    ]:
        canvas.bookmarkPage(key)
        canvas.addOutlineEntry(title, key, level=1)
        canvas.drawString(x, 700, title)
        for i in range(5):
            canvas.drawString(x, 675 - i * 20, text)
    canvas.drawString(290, 35, "51")
    canvas.save()
    chunks = ingest_route_a(pdf, probe(pdf))
    left = next(c for c in chunks if c.section_path.endswith("LEFT RULE"))
    right = next(c for c in chunks if c.section_path.endswith("RIGHT RULE"))
    assert "Left rule costs three sparks." in left.raw_text
    assert "Right rule" not in left.raw_text
    assert "Right rule restores two sparks." in right.raw_text
    assert "Left rule" not in right.raw_text
    # Un folio isolé sur un PDF d'une page ne suffit pas à mesurer un run.
    assert left.book_page is None and right.book_page is None
