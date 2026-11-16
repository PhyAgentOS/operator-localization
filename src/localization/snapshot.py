"""Optional local diagnostic snapshots. No UI, credentials, or robot-specific logic."""

import json
import math
import time
from pathlib import Path


def write_snapshot(folder, name, value):
    if not folder:
        return
    path = Path(folder) / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({**value, "issued_monotonic": time.monotonic()}, allow_nan=False)
    )
    temporary.replace(path)


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
