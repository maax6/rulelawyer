"""Vraies recherches BM25/Qdrant ; seuls les modèles neuronaux sont doublés."""

import json
import subprocess
from pathlib import Path
from typing import Any

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
            0.95 if "3 étincelles" in text and "dragon" not in question else 0.01
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
            assert hits[0].chunk.book_page == 42
            assert hits[0].bm25_rank is not None
            assert hits[0].dense_rank is not None
            assert hits[0].rrf_score > 1 / 61
            # Aucun terme anglais dans le corpus : seule la branche dense
            # peut proposer cette section ; BM25 ne doit pas inventer un hit.
            semantic = index.search("crossing")
            assert semantic[0].chunk.book_page == 42
            assert semantic[0].bm25_rank is None
            assert index.search("Quel dé pour attaquer un dragon ?") == []


def test_rrf_promotes_a_result_supported_by_both_rankings() -> None:
    scores = reciprocal_rank_fusion([["a", "b"], ["c", "b"]])
    assert sorted(scores, key=lambda key: scores[key], reverse=True)[0] == "b"
    assert scores["b"] == pytest.approx(2 / 62)


def test_ask_cli_from_pdf_to_cited_answer_and_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    monkeypatch.setattr("rulelawyer.retrieval.BGEModels", SmallTestModels)
    calls = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        sources = json.loads(kwargs["input"])["sources"]
        source = next(s for s in sources if "3 étincelles" in s["text"])
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "structured_output": {
                        "found": True,
                        "citations": [
                            {
                                "source_id": source["source_id"],
                                "quote": (
                                    "Traverser un Pont de brume coûte "
                                    "exactement 3 étincelles."
                                ),
                            }
                        ],
                    },
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)
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
    assert len(calls) == 1
