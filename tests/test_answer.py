"""Contrat de sortie de Claude : citations contrôlées, jamais de page inventée."""

import json
import subprocess
from typing import Any

import pytest

from rulelawyer.answer import NOT_FOUND, answer_from_chunks
from rulelawyer.ingest import Chunk, ChunkPage
from rulelawyer.models import Route


@pytest.fixture
def section() -> Chunk:
    return Chunk(
        id="b" * 64,
        book_id="a" * 64,
        section_path="Règles > Repos",
        raw_text="Introduction.\nLe repos rend 2 étincelles.",
        text="Règles > Repos\n\nIntroduction.\nLe repos rend 2 étincelles.",
        book_page=42,
        pdf_page=2,
        route=Route.A,
        pages=[
            ChunkPage(pdf_page=2, book_page=42, text="Introduction."),
            ChunkPage(pdf_page=3, book_page=43, text="Le repos rend 2 étincelles."),
        ],
    )


def fake_claude(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command[:2] == ["claude", "-p"]
        assert command[command.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in command
        assert "--no-session-persistence" in command
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        assert "sources" in json.loads(kwargs["input"])
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "is_error": False,
                    "structured_output": payload,
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)


def test_answer_cites_the_actual_printed_page_within_a_section(
    section: Chunk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_claude(
        monkeypatch,
        {
            "found": True,
            "citations": [
                {
                    "source_id": section.id + ":3",
                    "quote": "Le repos rend 2 étincelles.",
                }
            ],
        },
    )
    assert answer_from_chunks("Que rend le repos ?", [section]) == (
        "Le repos rend 2 étincelles. (p. 43)"
    )


@pytest.mark.parametrize(
    "source_id,quote",
    [
        ("b" * 64 + ":2", "Le repos rend 2 étincelles."),
        ("inconnu:3", "Le repos rend 2 étincelles."),
        ("b" * 64 + ":3", "Le repos rend 8 étincelles."),
    ],
)
def test_answer_refuses_wrong_page_or_invented_quote(
    section: Chunk,
    monkeypatch: pytest.MonkeyPatch,
    source_id: str,
    quote: str,
) -> None:
    fake_claude(
        monkeypatch,
        {
            "found": True,
            "citations": [
                {
                    "source_id": source_id,
                    "quote": quote,
                }
            ],
        },
    )
    assert answer_from_chunks("Que rend le repos ?", [section]) == NOT_FOUND


def test_answer_refuses_missing_rule_and_does_not_call_claude_without_hits(
    section: Chunk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_claude(monkeypatch, {"found": False, "citations": []})
    assert answer_from_chunks("Quel dé de combat ?", [section]) == NOT_FOUND

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Claude ne doit pas être appelé sans source citable")

    monkeypatch.setattr(subprocess, "run", unexpected)
    assert answer_from_chunks("Quel dé de combat ?", []) == NOT_FOUND
    for page in section.pages:
        page.book_page = None
    assert answer_from_chunks("Quel dé de combat ?", [section]) == NOT_FOUND
