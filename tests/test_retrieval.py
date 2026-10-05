"""Vraies recherches BM25/Qdrant ; seuls les modèles neuronaux sont doublés."""

import io
import json
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("bm25s")

from scripts.make_demo_pdf import make_demo_pdf
from typer.testing import CliRunner

from rulelawyer.cli import app
from rulelawyer.ingest import ingest_route_a
from rulelawyer.probe import probe
from rulelawyer.retrieval import open_index, reciprocal_rank_fusion


class SmallTestModels:
    name = "test-semantic-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if "Pont de brume" in text or "crossing" in text else [0.0, 1.0]
            for text in texts
        ]

    def rerank(self, question: str, texts: list[str]) -> list[float]:
        return [
            0.95 if "3 étincelles" in text and "dragon" not in question else 0.00001
            for text in texts
        ]


def test_hybrid_search_reopens_local_index_and_rejects_unrelated_question(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    chunks = ingest_route_a(pdf, probe(pdf))
    for _ in range(2):
        with open_index(chunks, tmp_path / "qdrant", SmallTestModels()) as index:
            hits = index.search("Combien coûte un Pont de brume ?")
            assert hits[0].page.book_page == 42
            assert hits[0].bm25_rank is not None
            assert hits[0].dense_rank is not None
            assert hits[0].rrf_score > 1 / 61
            # Aucun terme anglais dans le corpus : seule la branche dense
            # peut proposer cette section ; BM25 ne doit pas inventer un hit.
            semantic = index.search("crossing")
            assert semantic[0].page.book_page == 42
            assert semantic[0].bm25_rank is None
            assert index.search("Quel dé pour attaquer un dragon ?") == []


def test_rrf_promotes_a_result_supported_by_both_rankings() -> None:
    scores = reciprocal_rank_fusion([["a", "b"], ["c", "b"]])
    assert sorted(scores, key=lambda key: scores[key], reverse=True)[0] == "b"
    assert scores["b"] == pytest.approx(2 / 62)


def test_retrieval_selects_printed_page_inside_multileaf_section_without_llm(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    first, second, *_ = ingest_route_a(pdf, probe(pdf))
    section = first.model_copy(
        update={
            "text": first.text + "\n\n" + second.text,
            "raw_text": first.raw_text + "\n\n" + second.raw_text,
            "pages": first.pages + second.pages,
        }
    )
    with open_index([section], tmp_path / "qdrant", SmallTestModels()) as index:
        hit = index.search("Combien coûte un Pont de brume ?")[0]
    assert hit.chunk.book_page == 41
    assert hit.page.book_page == 42
    assert hit.page.pdf_page == 2
    assert "3 étincelles" in hit.page.text


def test_ask_cli_from_pdf_to_cited_answer_and_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    monkeypatch.setattr("rulelawyer.retrieval.BGEModels", SmallTestModels)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-not-a-secret")
    calls = []

    def request(req: Request, **kwargs: Any) -> io.BytesIO:
        assert isinstance(req.data, bytes)
        payload = json.loads(req.data)
        source = json.loads(payload["messages"][1]["content"])
        assert "3 étincelles" in source["passage"]
        calls.append(req)
        content = json.dumps(
            {
                "established": True,
                "answer": "Traverser un Pont de brume coûte exactement 3 étincelles.",
            }
        )
        return io.BytesIO(
            json.dumps(
                {
                    "choices": [{"message": {"content": content}}],
                }
            ).encode()
        )

    monkeypatch.setattr("rulelawyer.answer.urlopen", request)
    runner = CliRunner()
    options = ["--cache-dir", str(tmp_path / "index")]
    result = runner.invoke(
        app, ["ask", str(pdf), "Combien coûte un Pont de brume ?", *options]
    )
    assert result.exit_code == 0, result.output
    assert "3 étincelles. (p. 42)" in result.output
    refusal = runner.invoke(
        app, ["ask", str(pdf), "Quel dé pour attaquer un dragon ?", *options]
    )
    assert refusal.exit_code == 0, refusal.output
    assert "Ce n'est pas dans le manuel." in refusal.output
    monkeypatch.delenv("OPENROUTER_API_KEY")
    missing_key = runner.invoke(
        app, ["ask", str(pdf), "Combien coûte un Pont de brume ?", *options, "--debug"]
    )
    assert missing_key.exit_code == 1
    assert "book_page=42" in missing_key.output
    assert "OPENROUTER_API_KEY absente" in missing_key.output
    assert len(calls) == 1


def test_query_admission_keeps_complementary_pages_and_deduplicates_overlap(
    tmp_path: Path,
) -> None:
    from hashlib import sha256

    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    chunks = ingest_route_a(pdf, probe(pdf))
    bridge = chunks[1]
    duplicate = bridge.model_copy(update={"id": sha256(b"overlap").hexdigest()})

    class CompoundModels(SmallTestModels):
        def rerank(self, question: str, texts: list[str]) -> list[float]:
            return [0.95 if "3 étincelles" in t else 0.02 for t in texts]

    with open_index(
        [*chunks, duplicate], tmp_path / "qdrant", CompoundModels()
    ) as index:
        hits = index.search("Pont de brume et repos", threshold=0.5)
        assert hits[0].page.book_page == 42
        assert len(hits) == 4
        assert len({h.page.pdf_page for h in hits}) == 4
        assert any(h.page.book_page == 43 and h.score < 0.5 for h in hits)
        assert index.search("Pont de brume", threshold=0.99) == []
        assert len(index.search("Pont de brume", top_k=1)) == 1
