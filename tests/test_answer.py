"""Contrat HTTP simulé ; les folios sont fixés avant toute génération."""

import io
import json
from typing import Any
from urllib.request import Request

import pytest

from rulelawyer.answer import NOT_ESTABLISHED, NOT_FOUND, answer_from_passage
from rulelawyer.ingest import ChunkPage


@pytest.fixture
def passage() -> ChunkPage:
    return ChunkPage(pdf_page=3, book_page=43, text="Le repos rend 2 étincelles.")


def fake_openrouter(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-not-a-secret")

    def request(req: Request, **kwargs: Any) -> io.BytesIO:
        assert req.full_url == "https://openrouter.ai/api/v1/chat/completions"
        assert req.get_method() == "POST"
        assert req.get_header("Authorization") == "Bearer test-only-not-a-secret"
        assert isinstance(req.data, bytes)
        payload = json.loads(req.data)
        assert payload["model"] == "openai/gpt-4o-mini"
        assert payload["temperature"] == 0
        source = json.loads(payload["messages"][1]["content"])
        assert set(source) == {"question", "passage"}
        return io.BytesIO(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"established": True, "answer": answer}
                                )
                            }
                        }
                    ]
                }
            ).encode()
        )

    monkeypatch.setattr("rulelawyer.answer.urlopen", request)


def test_answer_copies_retrieval_book_page_without_asking_model_for_page(
    passage: ChunkPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_openrouter(monkeypatch, "Le repos rend 2 étincelles.")
    assert answer_from_passage("Que rend le repos ?", passage) == (
        "Le repos rend 2 étincelles. (p. 43)"
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Le repos rend 2 étincelles. (p. 3)",
        "Le repos rend 2 étincelles. (page 99)",
        "Le repos rend 2 étincelles. (pp. 43-99)",
        "Le repos rend 8 étincelles.",
    ],
)
def test_answer_rejects_wrong_page_or_invented_mechanics(
    passage: ChunkPage,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
) -> None:
    fake_openrouter(monkeypatch, answer)
    assert answer_from_passage("Que rend le repos ?", passage) == NOT_ESTABLISHED


def test_model_cannot_cite_partial_number_as_if_it_were_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passage = ChunkPage(
        pdf_page=2, book_page=42, text="Le passage coûte 13 étincelles."
    )
    fake_openrouter(monkeypatch, "3 étincelles.")
    assert answer_from_passage("Combien ?", passage) == NOT_ESTABLISHED


def test_matching_model_page_is_removed_and_citation_is_added_by_code(
    passage: ChunkPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_openrouter(monkeypatch, "Le repos rend 2 étincelles. (p. 43)")
    assert answer_from_passage("Que rend le repos ?", passage) == (
        "Le repos rend 2 étincelles. (p. 43)"
    )


def test_no_network_without_key_or_without_citable_passage(
    passage: ChunkPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Aucun appel réseau autorisé")

    monkeypatch.setattr("rulelawyer.answer.urlopen", unexpected)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY absente"):
        answer_from_passage("Que rend le repos ?", passage)
    assert answer_from_passage("Quel dé de combat ?", None) == NOT_FOUND
    passage.book_page = None
    assert answer_from_passage("Que rend le repos ?", passage) == NOT_ESTABLISHED
