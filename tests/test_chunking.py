"""Bornes sémantiques, chevauchement et folios des sous-chunks."""

import pytest

from rulelawyer.chunking import estimate_tokens, split_section
from rulelawyer.ingest import ChunkPage


def test_short_section_keeps_all_pages_together() -> None:
    pages = [
        ChunkPage(pdf_page=2, book_page=42, text="Rule on first page."),
        ChunkPage(pdf_page=3, book_page=43, text="Continuation."),
    ]
    assert split_section(pages, "Manual > Rule") == [pages]


def test_long_section_splits_on_headings_without_losing_page_provenance() -> None:
    pages = [
        ChunkPage(
            pdf_page=2,
            book_page=42,
            text="CROSSING\n" + "The crossing costs three sparks. " * 6,
        ),
        ChunkPage(
            pdf_page=3,
            book_page=43,
            text="RESTING\n" + "Rest restores two sparks. " * 6,
        ),
    ]
    groups = split_section(pages, "Manual > Journey", max_tokens=90)
    assert len(groups) == 2
    assert [group[0].book_page for group in groups] == [42, 43]
    assert groups[0][0].text.startswith("CROSSING")
    assert groups[1][0].text.startswith("RESTING")
    assert all(
        estimate_tokens("Manual > Journey\n\n" + "\n\n".join(p.text for p in group))
        <= 90
        for group in groups
    )


def test_paragraph_fallback_overlaps_whole_sentences_and_preserves_tail() -> None:
    text = " ".join(f"Rule {i} grants one spark." for i in range(40))
    page = ChunkPage(pdf_page=9, book_page=101, text=text)
    groups = split_section([page], "Rules", max_tokens=90)
    assert len(groups) > 3
    assert groups[-1][0].text.endswith("Rule 39 grants one spark.")
    assert all(p.pdf_page == 9 and p.book_page == 101 for g in groups for p in g)
    first, second = groups[0][0].text, groups[1][0].text
    assert first.split(". ")[-1].rstrip(".") == second.split(". ")[0]
    for i in range(40):
        assert any(f"Rule {i} grants one spark." in g[0].text for g in groups)


def test_indivisible_long_line_is_rejected_without_truncation() -> None:
    page = ChunkPage(pdf_page=1, book_page=None, text="x" * 1000)
    with pytest.raises(ValueError, match="indivisible"):
        split_section([page], "Rules", max_tokens=50)


def test_overlap_retains_whole_paragraph_when_it_exceeds_the_target() -> None:
    page = ChunkPage(
        pdf_page=1,
        book_page=41,
        text="\n\n".join(f"Paragraph {i}: " + "a" * 109 for i in range(8)),
    )
    groups = split_section([page], "Rules", max_tokens=100)
    assert groups[0][0].text.split("\n\n")[-1] == groups[1][0].text.split("\n\n")[0]
    assert all(estimate_tokens("Rules\n\n" + g[0].text) <= 100 for g in groups)
