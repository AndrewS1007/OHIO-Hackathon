import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOCK_DIR = ROOT / "data" / "mock"
DATA_DIR = Path(os.environ.get("CONCIERGE_DATA_DIR", MOCK_DIR))
LOG_DIR = ROOT / "logs"
