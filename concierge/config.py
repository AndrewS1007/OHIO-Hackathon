import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOCK_DIR = ROOT / "data" / "mock"
REAL_DIR = ROOT / "data" / "real"  # built by `python -m concierge.realdata`
DATA_DIR = Path(os.environ.get("CONCIERGE_DATA_DIR", REAL_DIR if (REAL_DIR / "devices.csv").exists() else MOCK_DIR))
LOG_DIR = ROOT / "logs"
