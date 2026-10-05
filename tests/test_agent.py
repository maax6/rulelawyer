"""Vrai SDK Anthropic sur transport HTTP simulé, sans clé ni réseau réels."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("anthropic")
pytest.importorskip("qdrant_client")
pytest.importorskip("bm25s")

import anthropic
from scripts.make_demo_pdf import make_demo_pdf
from typer.testing import CliRunner

from rulelawyer.agent import answer_with_anthropic, validate_reply
from rulelawyer.answer import NOT_ESTABLISHED
from rulelawyer.cli import app
from rulelawyer.evaluation import EvalQuestion, evaluate
from rulelawyer.ingest import ingest_route_a
from rulelawyer.probe import probe
from rulelawyer.retrieval import SearchHit


@pytest.fixture
def hits(tmp_path: Path) -> list[SearchHit]:
    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    chunks = ingest_route_a(pdf, probe(pdf))
    return [SearchHit(c, c.pages[0], 0.9, 0.03, 1, 1) for c in chunks[1:3]]


def reply(hits: list[SearchHit]) -> str:
    return json.dumps(
        {
            "kind": "answer",
            "claims": [
                {"source": n, "quote": h.page.text, "text": h.page.text}
                for n, h in enumerate(hits)
            ],
        }
    )


def test_sdk_sends_multiple_proofs_and_code_assigns_each_citation(
    hits: list[SearchHit],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = anthropic.Anthropic
    calls = []

    def request(req: httpx.Request) -> httpx.Response:
        payload = json.loads(req.content)
        assert payload["model"] == "claude-sonnet-4-6"
        assert payload["temperature"] == 0
        source = json.loads(payload["messages"][0]["content"])
        assert len(source["evidence"]) == 2
        assert all("book_page" not in e for e in source["evidence"])
        assert source["history"] == [["Question précédente", "Réponse précédente"]]
        calls.append(req)
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": payload["model"],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 30, "output_tokens": 30},
                "content": [{"type": "text", "text": reply(hits)}],
            },
        )

    def client(**kwargs: Any) -> anthropic.Anthropic:
        return factory(
            http_client=httpx.Client(transport=httpx.MockTransport(request)), **kwargs
        )

    monkeypatch.setattr(anthropic, "Anthropic", client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-secret")
    answer = answer_with_anthropic(
        "Coût et repos ?", hits, history=[("Question précédente", "Réponse précédente")]
    )
    assert "(p. 42)" in answer and "(p. 43)" in answer
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["source", "number", "quote", "page"])
def test_agent_rejects_invalid_proofs(hits: list[SearchHit], change: str) -> None:
    claim = {"source": 0, "quote": hits[0].page.text, "text": hits[0].page.text}
    if change == "source":
        claim["source"] = 6
    elif change == "number":
        claim["text"] = "La traversée coûte 999 étincelles."
    elif change == "quote":
        claim["quote"] = "Une règle de magie absente du manuel."
    else:
        claim["text"] = str(claim["text"]) + " (p. 999)"
    assert (
        validate_reply(json.dumps({"kind": "answer", "claims": [claim]}), hits)
        == NOT_ESTABLISHED
    )


def test_missing_key_stops_before_sdk_and_clarification_has_no_citation(
    hits: list[SearchHit],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY absente"):
        answer_with_anthropic("Coût ?", hits)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY absente"):
        answer_with_anthropic("Absent ?", [])
    question = "De quel objet voulez-vous connaître le coût ?"
    assert (
        validate_reply(json.dumps({"kind": "clarification", "reply": question}), hits)
        == question
    )
    assert (
        validate_reply(
            json.dumps({"kind": "clarification", "reply": "Coûte 999 ?"}), hits
        )
        == NOT_ESTABLISHED
    )
    refusal = validate_reply(json.dumps({"kind": "refusal"}), hits)
    assert refusal.startswith(NOT_ESTABLISHED) and "Section proche" in refusal
    assert "(p." not in refusal


def test_evaluation_checks_all_provided_pages_for_cross_section_answers(
    hits: list[SearchHit],
) -> None:
    class KnownIndex:
        def search(
            self, question: str, *, threshold: float = 0.1, top_k: int = 6
        ) -> list[SearchHit]:
            return hits

    question = EvalQuestion(
        id="cross",
        category="cross_section",
        question="Coût et repos ?",
        expected_pages=[42, 43],
        must_contain=["3", "7"],
        must_not_hallucinate=["999"],
    )
    report = evaluate(
        [question],
        KnownIndex(),
        generate_from_hits=lambda q, h: validate_reply(reply(h), h),
    )
    assert report.answer_pass_rate == report.citation_exact_rate == 1
    incomplete = evaluate(
        [question],
        KnownIndex(),
        generate_from_hits=lambda q, h: validate_reply(reply(h[:1]), h),
    )
    assert incomplete.answer_pass_rate == 0


def test_repl_reuses_index_and_keeps_clearable_history_in_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    builds = []
    histories = []

    class Models:
        name = "repl-test"

        def embed(self, texts: list[str]) -> list[list[float]]:
            if len(texts) > 1:
                builds.append(texts)
            return [[1.0, 0.0] for _ in texts]

        def rerank(self, question: str, texts: list[str]) -> list[float]:
            return [0.9] * len(texts)

    def answer(
        q: str,
        h: list[SearchHit],
        provider: Any,
        history: list[tuple[str, str]] | None = None,
    ) -> str:
        histories.append(list(history or []))
        return "Réponse simulée (p. 42)"

    monkeypatch.setattr("rulelawyer.retrieval.BGEModels", Models)
    monkeypatch.setattr("rulelawyer.cli._answer", answer)
    result = CliRunner().invoke(
        app,
        ["repl", str(pdf), "--cache-dir", str(tmp_path / "cache"), "--debug"],
        input="Coût ?\n/history\nRepos ?\n/clear\nCoût ?\n/quit\n",
    )
    assert result.exit_code == 0, result.output
    assert len(builds) == 1
    assert histories == [[], [("Coût ?", "Réponse simulée (p. 42)")], []]
    assert "book_page=" in result.output and "Réponse simulée" in result.output


@pytest.mark.parametrize(
    "status,reason,content",
    [
        (503, "end_turn", "private provider error"),
        (200, "max_tokens", "{}"),
        (200, "end_turn", "not JSON"),
    ],
)
def test_sdk_errors_and_truncation_are_explicit_without_provider_body(
    hits: list[SearchHit],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    reason: str,
    content: str,
) -> None:
    factory = anthropic.Anthropic

    def request(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "stop_reason": reason,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "content": [{"type": "text", "text": content}],
            },
        )

    def client(**kwargs: Any) -> anthropic.Anthropic:
        return factory(
            http_client=httpx.Client(transport=httpx.MockTransport(request)), **kwargs
        )

    monkeypatch.setattr(anthropic, "Anthropic", client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-secret")
    with pytest.raises(RuntimeError) as error:
        answer_with_anthropic("Coût ?", hits)
    assert "private provider error" not in str(error.value)


def test_agent_clarifies_without_evidence_and_limits_refusal_to_known_text() -> None:
    clarification = json.dumps({"kind": "clarification", "reply": "Which item?"})
    assert validate_reply(clarification, []) == "Which item?"
    english = json.dumps({"kind": "refusal", "reply": "This is not in the manual."})
    assert validate_reply(english, []) == "This is not in the manual."
    invented = json.dumps({"kind": "refusal", "reply": "Use magic to recharge."})
    assert validate_reply(invented, []) == NOT_ESTABLISHED
