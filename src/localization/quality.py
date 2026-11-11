"""Conservative scan-to-map agreement, independent of the AMCL covariance."""

import math

import numpy as np


def planar_pose(values):
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError("pose must be [x, y, yaw]")
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        for v in values
    ):
        raise ValueError("pose values must be finite numbers")
    x, y, yaw = map(float, values)
    return [x, y, math.atan2(math.sin(yaw), math.cos(yaw))]


def scan_agreement(grid, resolution, origin, endpoints, *, tolerance=0.25):
    """Fraction of finite obstacle returns near occupied cells; unknowns never match."""
    values = np.asarray(endpoints, dtype=float)
    if values.size == 0:
        return 0.0, 0
    values = values.reshape(-1, 2)
    c, s = math.cos(origin[2]), math.sin(origin[2])
    dx, dy = values[:, 0] - origin[0], values[:, 1] - origin[1]
    ix = np.floor((c * dx + s * dy) / resolution).astype(int)
    iy = np.floor((-s * dx + c * dy) / resolution).astype(int)
    height, width = grid.shape
    valid = (ix >= 0) & (iy >= 0) & (ix < width) & (iy < height)
    # Count outside-map returns as mismatches as well.
    matched = np.zeros(len(ix), dtype=bool)
    r = math.ceil(tolerance / resolution)
    for ox in range(-r, r + 1):
        for oy in range(-r, r + 1):
            if math.hypot(ox, oy) * resolution > tolerance:
                continue
            x, y = ix + ox, iy + oy
            ok = valid & (x >= 0) & (y >= 0) & (x < width) & (y < height)
            matched[ok] |= grid[y[ok], x[ok]] >= 65
    return float(matched.mean()), len(ix)


class AgreementGate:
    """Strict acquisition, relaxed tracking, bounded tolerance of brief occlusion.

    Sensor/TF/covariance failures bypass this class and remain immediate failures.
    """

    def __init__(self, *, acquire=0.70, track=0.65, recover=0.70, dwell=3.0, grace=0.8):
        if not 0 < track <= recover <= acquire <= 1 or dwell <= 0 or grace < 0:
            raise ValueError("invalid agreement thresholds")
        self.acquire, self.track, self.recover = acquire, track, recover
        self.dwell, self.grace = dwell, grace
        self.locked = False
        self.good_since = self.bad_since = None
        self.first_pose = None
        self.samples = set()

    def observe(self, agreement, beams, pose, *, now, sample_stamp):
        if beams < 40 or not math.isfinite(agreement) or agreement < 0.45:
            self.reset()
            return False
        if not self.locked:
            if agreement < self.acquire:
                self.reset()
                return False
            if self.first_pose is not None:
                if (
                    math.hypot(pose[0] - self.first_pose[0], pose[1] - self.first_pose[1]) > 0.12
                    or abs(
                        math.atan2(
                            math.sin(pose[2] - self.first_pose[2]),
                            math.cos(pose[2] - self.first_pose[2]),
                        )
                    )
                    > 0.12
                ):
                    self.reset()
            if self.good_since is None:
                self.good_since, self.first_pose = now, list(pose)
            self.samples.add(sample_stamp)
            self.locked = now - self.good_since >= self.dwell and len(self.samples) >= 3
            return self.locked
        if agreement >= self.recover:
            self.bad_since = None
        elif agreement < self.track:
            if self.bad_since is None:
                self.bad_since = now
            if now - self.bad_since > self.grace:
                self.reset()
                return False
        return True

    def reset(self):
        self.locked = False
        self.good_since = self.bad_since = self.first_pose = None
        self.samples.clear()
