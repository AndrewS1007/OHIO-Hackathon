import os
from pathlib import Path

# The unit tests use the mock dataset's fixed IDs (C14, Maria's scripted storm).
# Set before anything imports concierge.config, which reads it once.
os.environ.setdefault("CONCIERGE_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "mock"))

# Never let tests touch the real session/transcript database.
import tempfile
os.environ["CONCIERGE_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")

# Never call the real local model from tests: point Ollama at a closed port so the
# chatbot falls back to Claude-fake or keyword mode.
os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"

import pytest


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch):
    """agent.py hard-codes OLLAMA_URL, so the env var above isn't enough: patch the module too."""
    from concierge import agent
    monkeypatch.setattr(agent, "OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(agent, "_ollama_seen", (0.0, False))
