import os
from pathlib import Path

# The unit tests use the mock dataset's fixed IDs (C14, Maria's scripted storm).
# Set before anything imports concierge.config, which reads it once.
os.environ.setdefault("CONCIERGE_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data" / "mock"))
