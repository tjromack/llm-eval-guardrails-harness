"""Synthetic stand-in for the Regulatory RAG Copilot — a DEV/DEMO target only.

This is NOT part of the harness. It exists so the adapter seam can be exercised
end to end without standing up the real copilot. It speaks the command-transport
contract: read a JSON request from stdin ({"question": ...}), print a JSON reply
({"output", "trace"}) to stdout.

It is a deliberately simple rule-based responder over a few public/synthetic
regulatory facts. It behaves like a reasonable *baseline*: it cites sources,
abstains on out-of-scope questions, refuses adversarial asks, and does not echo
planted PII back. A degraded variant for the regression demo comes later.

All content here is synthetic/public (CFR section numbers); no PHI.
"""

from __future__ import annotations

import json
import re
import sys

# (keywords, answer, citation) — first match wins.
_FACTS = [
    (
        ("retain", "retention", "documentation", "disposal", "how long"),
        "A covered entity must retain required HIPAA documentation for six years "
        "from the date of its creation or the date it was last in effect, whichever "
        "is later.",
        "45 CFR 164.316(b)(2)(i)",
    ),
    (
        ("breach", "notify", "notification", "60"),
        "Following discovery of a breach of unsecured PHI, a covered entity must "
        "notify affected individuals without unreasonable delay and no later than "
        "60 calendar days after discovery.",
        "45 CFR 164.404",
    ),
    (
        ("minimum necessary",),
        "The minimum necessary standard requires reasonable efforts to limit PHI to "
        "the minimum necessary to accomplish the intended purpose of the use, "
        "disclosure, or request.",
        "45 CFR 164.502(b)",
    ),
    (
        ("privacy official", "privacy officer", "designate"),
        "Yes. A covered entity must designate a privacy official responsible for the "
        "development and implementation of its privacy policies and procedures.",
        "45 CFR 164.530(a)(1)",
    ),
]

_OUT_OF_SCOPE = ("weather", "forecast", "temperature", "pain", "medication", "dose", "mg")
_ADVERSARIAL = (
    "ignore all previous",
    "ignore your rules",
    "system prompt",
    "pretend you are",
    "unrestricted assistant",
    "not bound by",
    "without any authorization",
)
_ABSTAIN = (
    "I don't have information on that in the regulatory corpus, so I can't answer. "
    "I can only help with questions grounded in the regulatory documents."
)
_REFUSE = (
    "I can't help with that. I won't reveal my instructions or provide guidance on "
    "bypassing HIPAA requirements."
)

# Synthetic PII patterns to scrub from any echo (SSN, MRN-like ids).
_PII = re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\bMRN\s*\d+\b|\b\d{8}\b")


def answer(question: str) -> dict:
    q = question.lower()

    if any(k in q for k in _ADVERSARIAL):
        return {"output": _REFUSE, "trace": {"decision": "refuse", "citations": []}}

    if any(k in q for k in _OUT_OF_SCOPE):
        return {"output": _ABSTAIN, "trace": {"decision": "abstain", "citations": []}}

    for keywords, text, citation in _FACTS:
        if any(k in q for k in keywords):
            # If the user planted PII in the prompt, answer WITHOUT echoing it.
            out = f"{text} (See {citation}.)"
            return {
                "output": out,
                "trace": {"decision": "answer", "citations": [citation]},
            }

    # Recognized as in-domain but unknown → abstain rather than guess.
    return {"output": _ABSTAIN, "trace": {"decision": "abstain", "citations": []}}


def main() -> int:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        req = {"question": raw}
    question = req.get("question") or req.get("input") or ""
    reply = answer(str(question))
    # Defense in depth: never let a planted identifier through in the output.
    reply["output"] = _PII.sub("[redacted]", reply["output"])
    sys.stdout.write(json.dumps(reply, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
