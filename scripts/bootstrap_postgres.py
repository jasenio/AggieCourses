#!/usr/bin/env python
"""Load the committed CSV snapshots into PostgreSQL and build read models."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.postgres import bootstrap


if __name__ == "__main__":
    print(json.dumps(bootstrap(ROOT_DIR / "data"), indent=2, sort_keys=True))
