"""Adapter for the Regulatory RAG Copilot — the first wired target (Decision 002).

Two transports, chosen in .env so the harness never assumes how the copilot is
deployed:
  - "http"    : POST the case input as JSON to RAG_COPILOT_URL.
  - "command" : run RAG_COPILOT_CMD as a subprocess, pass input as JSON on stdin.

Either way the target is expected to return a JSON object; we read the answer
from `output`/`answer`/`response` and a trace from `trace` (or assemble one from
`citations`/`context`/`sources`). A non-JSON / plain-text reply is treated as the
output with an empty trace. The adapter stays thin: it shapes I/O and times the
call; it does not score anything.
"""

from __future__ import annotations

import json
import subprocess
from time import perf_counter
from typing import Any

import httpx

from app.adapters.base import Adapter, TargetResult, register

_OUTPUT_KEYS = ("output", "answer", "response", "text")
_TRACE_KEYS = ("citations", "context", "retrieved", "sources", "documents")


def _ms(t0: float) -> int:
    return int((perf_counter() - t0) * 1000)


def _as_payload(case_input: Any) -> dict[str, Any]:
    """Normalize a case input into the JSON the target receives."""
    if isinstance(case_input, str):
        return {"question": case_input}
    if isinstance(case_input, dict):
        return dict(case_input)
    return {"question": str(case_input)}


def _parse_payload(data: Any) -> tuple[str, dict[str, Any]]:
    """Pull (output, trace) out of a target's reply."""
    if isinstance(data, str):
        return data, {}
    if not isinstance(data, dict):
        return str(data), {}
    output = ""
    for k in _OUTPUT_KEYS:
        if isinstance(data.get(k), str):
            output = data[k]
            break
    trace = data.get("trace")
    if not isinstance(trace, dict):
        trace = {k: data[k] for k in _TRACE_KEYS if k in data}
    return output, trace


@register("rag_copilot")
class RagCopilotAdapter(Adapter):
    def __init__(
        self,
        transport: str | None = None,
        url: str | None = None,
        cmd: str | None = None,
        version: str | None = None,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ):
        # Defaults come from settings; explicit args let tests/callers override.
        from app.config import settings

        self.transport = (transport or settings.rag_adapter).lower()
        self.url = url or settings.rag_url
        self.cmd = cmd or settings.rag_cmd
        self._version = version or settings.rag_version
        self.timeout = timeout
        self._http_client = http_client  # injected in tests; else created per call

    @property
    def target_version(self) -> str:
        return self._version

    def run(self, case_input: Any) -> TargetResult:
        t0 = perf_counter()
        try:
            if self.transport == "http":
                output, trace = self._run_http(case_input)
            elif self.transport == "command":
                output, trace = self._run_command(case_input)
            else:
                return TargetResult(
                    output="",
                    target_version=self._version,
                    latency_ms=_ms(t0),
                    error=f"unknown transport {self.transport!r} (use 'http' or 'command')",
                )
        except Exception as e:  # transport/target failure → reported, never raised
            return TargetResult(
                output="",
                trace={"transport": self.transport},
                target_version=self._version,
                latency_ms=_ms(t0),
                error=f"{type(e).__name__}: {e}",
            )
        trace.setdefault("transport", self.transport)
        return TargetResult(
            output=output,
            trace=trace,
            target_version=self._version,
            latency_ms=_ms(t0),
        )

    # -- transports ---------------------------------------------------------

    def _run_http(self, case_input: Any) -> tuple[str, dict[str, Any]]:
        payload = _as_payload(case_input)
        client = self._http_client or httpx.Client(timeout=self.timeout)
        close = self._http_client is None
        try:
            resp = client.post(self.url, json=payload)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            data: Any = resp.json() if "json" in ctype else resp.text
        finally:
            if close:
                client.close()
        return _parse_payload(data)

    def _run_command(self, case_input: Any) -> tuple[str, dict[str, Any]]:
        if not self.cmd:
            raise ValueError("RAG_COPILOT_CMD is empty; set it for the command transport")
        payload = json.dumps(_as_payload(case_input))
        proc = subprocess.run(
            self.cmd,
            shell=True,
            input=payload,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"target command exited {proc.returncode}: {proc.stderr.strip()[:300]}"
            )
        stdout = proc.stdout.strip()
        try:
            data: Any = json.loads(stdout)
        except json.JSONDecodeError:
            data = stdout  # plain-text reply → treat as the output
        return _parse_payload(data)


# ---- CLI: call the target for one question and print the captured result ----


def _main(argv: list[str]) -> int:
    question = argv[0] if argv else "How long must HIPAA documentation be retained?"
    adapter = RagCopilotAdapter()
    result = adapter.run(question)
    print(f"transport     {adapter.transport}")
    print(f"target_version {result.target_version}")
    print(f"latency_ms    {result.latency_ms}")
    if not result.ok:
        print(f"ERROR         {result.error}")
        return 1
    print(f"output        {result.output}")
    print(f"trace         {json.dumps(result.trace, ensure_ascii=False)[:500]}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
