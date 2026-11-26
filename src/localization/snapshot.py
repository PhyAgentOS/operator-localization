"""Optional local diagnostic snapshots. No UI, credentials, or robot-specific logic."""

import json
import math
import tempfile
import time
from pathlib import Path


def write_snapshot(folder, name, value):
    if not folder:
        return
    path = Path(folder) / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize before allocating a temporary file: invalid values leave the
    # previous snapshot untouched. Every call owns its temporary file, including
    # concurrent writers in one process or in different node processes.
    payload = json.dumps({**value, "issued_monotonic": time.monotonic()}, allow_nan=False)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        # Same-directory rename publishes one complete JSON document atomically.
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pose_value(pose):
    p, q = pose.position, pose.orientation
    return [p.x, p.y, math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))]


def grid_value(msg):
    m = msg.info
    return {
        "width": m.width,
        "height": m.height,
        "resolution": m.resolution,
        "origin": pose_value(m.origin),
        "frame_id": msg.header.frame_id,
        "data": list(msg.data),
    }
