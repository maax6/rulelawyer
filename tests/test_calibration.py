"""Le seuil est calculé sur des preuves distinctes, sans lire l'éval du manuel."""

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("bm25s")

from eval.calibrate_retrieval import Case, Corpus, Passage, calibrate


class CalibrationModels:
    name = "calibration-test"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("La calibration ne doit pas encoder le manuel.")

    def rerank(self, question: str, texts: list[str]) -> list[float]:
        if question == "multi":
            return [0.035, 0.0002]
        if question == "foreign":
            return [0.00001, 0.00002]
        return [0.6, 0.003]


def test_threshold_uses_positive_evidence_not_an_unrelated_top_score() -> None:
    corpus = Corpus(
        passages=[
            Passage(page=11, section="A", text="Rule A"),
            Passage(page=12, section="B", text="Rule B"),
        ],
        cases=[
            Case(question="single", pages=[12]),
            Case(question="multi", pages=[11, 12]),
            Case(question="foreign", pages=[]),
        ],
    )
    result = calibrate(corpus, CalibrationModels(), "test-digest")
    assert result.recommended_threshold == 0.001
    assert result.minimum_positive_score == 0.003
    assert result.positive_queries_admitted == 2
    assert result.negative_queries_refused == 1
    corpus.cases[0].pages = [99]
    with pytest.raises(ValueError, match="absente"):
        calibrate(corpus, CalibrationModels(), "test-digest")


def test_balanced_calibration_favors_refusal_when_candidates_tie() -> None:
    corpus = Corpus(
        passages=[Passage(page=11, section="A", text="Rule A")],
        cases=[
            Case(question="clear", pages=[11]),
            Case(question="weak", pages=[11]),
            Case(question="distractor", pages=[]),
            Case(question="unrelated", pages=[]),
        ],
    )

    class BalancedModels(CalibrationModels):
        def rerank(self, question: str, texts: list[str]) -> list[float]:
            return [
                {
                    "clear": 0.25,
                    "weak": 0.014,
                    "distractor": 0.096,
                    "unrelated": 0.00001,
                }[question]
            ]

    result = calibrate(corpus, BalancedModels(), "test-digest")
    assert result.recommended_threshold == 0.1
    assert result.balanced_accuracy == 0.75
    assert result.positive_queries_admitted == 1
    assert result.negative_queries_refused == 2
