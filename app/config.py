"""Central configuration, loaded from the environment (.env).

One small, single-purpose place to read settings so no module reaches into
os.environ directly. Secrets live in .env (never committed); see .env.example.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env once on import. Harmless if the file is absent (uses defaults).
load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # Judge (Phase 5)
    model_provider: str = _get("MODEL_PROVIDER", "anthropic")
    anthropic_api_key: str = _get("ANTHROPIC_API_KEY", "")
    judge_model: str = _get("JUDGE_MODEL", "claude-opus-4-8")
    ollama_base_url: str = _get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = _get("OLLAMA_MODEL", "llama3.1")

    # Judge stability (2026-07-28): the judge is non-deterministic on borderline cases, so a single
    # run is not a measurement. JUDGE_RUNS repeats ONLY the judged step per check and reports the
    # distribution + a stability flag (rule checks never repeat). A judged score within
    # JUDGE_UNSTABLE_BAND of the pass threshold is flagged "near-threshold" even in a single run.
    judge_runs: int = max(1, int(_get("JUDGE_RUNS", "1")))
    judge_unstable_band: float = float(_get("JUDGE_UNSTABLE_BAND", "0.05"))

    # Target adapter (Phase 2)
    rag_adapter: str = _get("RAG_COPILOT_ADAPTER", "http")
    rag_url: str = _get("RAG_COPILOT_URL", "http://localhost:8000/answer")
    rag_cmd: str = _get("RAG_COPILOT_CMD", "")
    rag_version: str = _get("RAG_COPILOT_VERSION", "baseline")

    # Storage
    db_path: str = _get("HARNESS_DB", "data/harness.db")

    # Server
    host: str = _get("HOST", "127.0.0.1")
    port: int = int(_get("PORT", "8000"))

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else REPO_ROOT / p


settings = Settings()
