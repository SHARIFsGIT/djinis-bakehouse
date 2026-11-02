"""Ensure the backend package is importable no matter where pytest runs from."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
