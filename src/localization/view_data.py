"""Atomic viewer state and portable occupancy-grid image rendering."""

import json
import os
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
    temporary.replace(path)
