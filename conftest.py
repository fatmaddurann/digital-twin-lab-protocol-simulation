"""
conftest.py — pytest configuration for Digital Twin Lab Protocol Simulation.

Ensures the project root is always on sys.path regardless of where pytest
is invoked from (local, CI, editable install, etc.), so all relative imports
resolve correctly without installing the package first.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root = directory containing this file
PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
