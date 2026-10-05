"""Métriques indépendantes : cas croisé partiel, refus et fausse citation."""

from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("bm25s")

from scripts.make_demo_pdf import make_demo_pdf

from rulelawyer.answer import NOT_FOUND
from rulelawyer.evaluation import EvalQuestion, evaluate, load_dataset
from rulelawyer.ingest import ChunkPage, ingest_route_a
from rulelawyer.probe import probe
from rulelawyer.retrieval import SearchHit


def test_metrics_separate_missing_generation_and_partial_page_recall(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "manual.pdf"
    make_demo_pdf(pdf)
    chunk = ingest_route_a(pdf, probe(pdf))[1]
    hit = SearchHit(chunk, chunk.pages[0], 0.9, 0.03, 1, 1)

    class KnownIndex:
        def search(
            self, question: str, *, threshold: float = 0.5, top_k: int = 6
        ) -> list[SearchHit]:
            return [] if question == "outside" else [hit]

    questions = [
        EvalQuestion(
            id="one",
            category="factual",
            question="cost",
            expected_pages=[42],
            must_contain=["3"],
            must_not_hallucinate=["8"],
        ),
        EvalQuestion(
            id="two",
            category="cross_section",
            question="cost and rest",
            expected_pages=[42, 43],
            must_contain=["3", "2"],
            must_not_hallucinate=[],
        ),
        EvalQuestion(
            id="out",
            category="out_of_book",
            question="outside",
            expected_pages=[],
            must_contain=[],
            must_not_hallucinate=[],
        ),
    ]
    report = evaluate(questions, KnownIndex())
    assert report.recall_at_6 == 0.75
    assert report.recall_by_category == {"factual": 1.0, "cross_section": 0.5}
    assert report.retrieval_refusal_rate == 1
    assert report.answer_pass_rate is None
    assert report.citation_exact_rate is None
    assert not report.retrieval_target_met

    def wrong_page(question: str, passage: ChunkPage | None) -> str:
        return "3 sparks (p. 43)" if passage is not None else NOT_FOUND

    generated = evaluate(questions, KnownIndex(), generate=wrong_page)
    assert generated.citation_exact_rate == 0
    assert generated.answer_pass_rate == 0
    assert generated.correct_refusal_rate == 1

    def wrong_number(question: str, passage: ChunkPage | None) -> str:
        return "13 sparks (p. 42)" if passage is not None else NOT_FOUND

    assert (
        evaluate(questions, KnownIndex(), generate=wrong_number).answer_pass_rate == 0
    )

    equivalent = questions[0].model_copy(
        update={
            "expected_pages": [101],
            "page_alternatives": {101: [42]},
            "must_contain": [],
            "must_contain_any": [["three", "3"]],
        }
    )

    def equivalent_page(question: str, passage: ChunkPage | None) -> str:
        return "3 sparks (p. 42)"

    accepted = evaluate([equivalent], KnownIndex(), generate=equivalent_page)
    assert accepted.recall_at_6 == 1
    assert accepted.citation_exact_rate == 1
    assert accepted.answer_pass_rate == 1
    assert accepted.cases[0].page_alternatives == {101: [42]}
    assert (
        evaluate([equivalent], KnownIndex(), generate=wrong_number).answer_pass_rate
        == 0
    )

    def unsupported_alternative(question: str, passage: ChunkPage | None) -> str:
        return "3 sparks (p. 101)"

    assert (
        evaluate(
            [equivalent], KnownIndex(), generate=unsupported_alternative
        ).citation_exact_rate
        == 0
    )

    def error(question: str, passage: ChunkPage | None) -> str:
        raise RuntimeError("private-provider-message")

    failed = evaluate(questions, KnownIndex(), generate=error)
    assert failed.generation_errors == 3
    assert "private-provider-message" not in failed.model_dump_json()
    assert failed.answer_pass_rate == 0


def test_published_dataset_has_thirty_balanced_cases_and_no_passages() -> None:
    dataset = load_dataset(Path("eval/questions.yaml"))
    assert len(dataset.questions) == 30
    assert dataset.book_name.startswith("Corporation")
