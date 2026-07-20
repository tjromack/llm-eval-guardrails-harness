#!/usr/bin/env python
"""Target shim: the harness's CLI contract -> the REAL Regulatory RAG Copilot.

The harness `command` adapter passes a case as JSON on **stdin** and expects a JSON
object back. The copilot's own CLI (`python -m app.answer "<question>" --json`) takes
the question as an **argv positional**. This ~40-line shim bridges the two, so the
harness can grade the actual copilot instead of the bundled synthetic stand-in.

It shells out to the copilot's own venv (rather than importing it) so the two
projects keep separate dependency trees — the adapter boundary stays honest.

Usage (set in the harness .env):
    RAG_COPILOT_ADAPTER=command
    RAG_COPILOT_CMD=python tools/real_rag_copilot.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

COPILOT_DIR = pathlib.Path(r"C:\ai\postpay-regulatory-copilot")
COPILOT_PY = COPILOT_DIR / ".venv" / "Scripts" / "python.exe"
TIMEOUT_S = 180


def _question(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ("question", "input", "query", "prompt"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return str(payload)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # empty/!JSON stdin -> treat as a bare question
        payload = sys.stdin.read()

    question = _question(payload)

    if not COPILOT_PY.exists():
        print(json.dumps({"answer": "", "error": f"copilot venv not found: {COPILOT_PY}"}))
        return

    proc = subprocess.run(
        [str(COPILOT_PY), "-m", "app.answer", question, "--json"],
        cwd=str(COPILOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_S,
    )

    out = (proc.stdout or "").strip()
    start = out.find("{")  # skip any banner/warning lines before the JSON
    if proc.returncode != 0 or start < 0:
        print(json.dumps({"answer": "", "error": (proc.stderr or "no JSON on stdout")[-600:]}))
        return

    # Pass the copilot's dict straight through: it already exposes `answer` and
    # `citations`, which is exactly what the adapter reads.
    print(out[start:])


if __name__ == "__main__":
    main()
