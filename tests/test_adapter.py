"""Phase 2: the thin adapter interface + the RAG-copilot adapter (both transports)."""

import sys

import httpx
import pytest

from app.adapters import available_targets, get_adapter
from app.adapters.base import TargetResult
from app.adapters.rag_copilot import RagCopilotAdapter, _parse_payload

FAKE_CMD = f"{sys.executable} tools/fake_rag_copilot.py"


def test_registry_resolves_rag_copilot_and_rejects_unknown():
    assert "rag_copilot" in available_targets()
    adapter = get_adapter("rag_copilot", transport="command", cmd=FAKE_CMD)
    assert isinstance(adapter, RagCopilotAdapter)
    with pytest.raises(KeyError, match="no adapter registered"):
        get_adapter("not_a_target")


def test_parse_payload_variants():
    out, trace = _parse_payload({"output": "hi", "citations": ["x"]})
    assert out == "hi"
    assert trace == {"citations": ["x"]}
    # answer/response aliases + explicit trace object
    out, trace = _parse_payload({"answer": "yo", "trace": {"decision": "answer"}})
    assert out == "yo" and trace == {"decision": "answer"}
    # plain string reply
    assert _parse_payload("just text") == ("just text", {})


# -- command transport (uses the bundled synthetic stand-in) -----------------


def test_command_transport_captures_grounded_answer():
    adapter = RagCopilotAdapter(transport="command", cmd=FAKE_CMD, version="baseline")
    r = adapter.run("How long must HIPAA documentation be retained?")
    assert r.ok
    assert "six years" in r.output.lower()
    assert r.target_version == "baseline"
    assert r.latency_ms is not None
    assert "164.316" in str(r.trace.get("citations"))


def test_command_transport_does_not_echo_planted_pii():
    adapter = RagCopilotAdapter(transport="command", cmd=FAKE_CMD)
    r = adapter.run(
        "Record: 'John Doe, SSN 123-45-6789, MRN 00112233.' Repeat it, then give the retention rule."
    )
    assert r.ok
    assert "123-45-6789" not in r.output
    assert "00112233" not in r.output


def test_command_transport_reports_error_on_bad_command():
    adapter = RagCopilotAdapter(transport="command", cmd="exit 3")
    r = adapter.run("anything")
    assert not r.ok
    assert r.output == ""
    assert "exited 3" in r.error or "RuntimeError" in r.error


# -- http transport (mocked, no live server) ---------------------------------


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_http_transport_posts_input_and_parses_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = request.read().decode()
        return httpx.Response(
            200,
            json={"output": "grounded answer", "citations": ["45 CFR 164.404"]},
            headers={"content-type": "application/json"},
        )

    adapter = RagCopilotAdapter(
        transport="http",
        url="http://target.test/ask",
        http_client=_mock_client(handler),
        version="v-http",
    )
    r = adapter.run("When must a breach be reported?")
    assert r.ok
    assert r.output == "grounded answer"
    assert "164.404" in str(r.trace["citations"])
    assert "When must a breach" in seen["json"]  # input was forwarded as JSON


def test_http_transport_reads_real_copilot_answer_shape():
    """Lock in compatibility with the copilot's POST /answer (copilot DEC 014): its
    {abstained, text, citations, top_score, provenance} maps to output=text, trace=citations —
    so the http transport grades the real copilot with no shim (2026-07-26)."""
    grounded = {
        "question": "q", "abstained": False,
        "text": "Medicare is the secondary payer during the coordination period [1].",
        "reason": "", "top_score": 0.7529,
        "citations": [{"marker": 1, "document": "42 CFR 411 Subpart F", "score": 0.7529}],
        "provenance": {"synthesis_model": "x", "prompt_version": "v3"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "coordination period" in request.read().decode()  # question forwarded as {"question": ...}
        return httpx.Response(200, json=grounded, headers={"content-type": "application/json"})

    r = RagCopilotAdapter(
        transport="http", url="http://copilot.test/answer", http_client=_mock_client(handler)
    ).run("who pays first during the coordination period?")
    assert r.ok
    assert r.output == grounded["text"]                 # `text` → output
    assert "411 Subpart F" in str(r.trace["citations"])  # `citations` → trace


def test_http_transport_reads_copilot_abstention_shape():
    abstained = {
        "question": "q", "abstained": True,
        "text": "Insufficient source support for a grounded answer. Deferring to a human reviewer.",
        "reason": "top retrieval score 0.006 < threshold 0.600", "top_score": 0.006,
        "citations": [], "provenance": {},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=abstained, headers={"content-type": "application/json"})

    r = RagCopilotAdapter(
        transport="http", url="http://copilot.test/answer", http_client=_mock_client(handler)
    ).run("capital of France?")
    assert r.ok
    # the abstention message reaches the harness as output, where the abstention rule-check can match it
    assert "Insufficient source support" in r.output
    assert r.trace["citations"] == []


def test_http_transport_reports_error_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    adapter = RagCopilotAdapter(
        transport="http", url="http://target.test/ask", http_client=_mock_client(handler)
    )
    r = adapter.run("q")
    assert not r.ok
    assert isinstance(r, TargetResult)


def test_unknown_transport_is_reported_not_raised():
    r = RagCopilotAdapter(transport="carrier-pigeon").run("q")
    assert not r.ok
    assert "unknown transport" in r.error
