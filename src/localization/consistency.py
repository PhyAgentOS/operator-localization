"""Latch unexplained map corrections while continuous odometry remains near rest."""

import math

from localization.quality import planar_pose


class StationaryConsistencyGuard:
    def __init__(self):
        self.anchor = None
        self.failed = False

    def observe(self, map_pose, odom_pose):
        m, o = planar_pose(map_pose), planar_pose(odom_pose)
        if self.failed:
            return False
        if self.anchor is None:
            self.anchor = m, o
            return True
        old_m, old_o = self.anchor
        dx, dy, da = o[0] - old_o[0], o[1] - old_o[1], o[2] - old_o[2]
        da = math.atan2(math.sin(da), math.cos(da))
        if math.hypot(dx, dy) > 0.08 or abs(da) > 0.08:
            self.anchor = m, o  # Normal motion: start a new near-rest interval.
            return True
        a = old_m[2] - old_o[2]
        expected = [
            old_m[0] + math.cos(a) * dx - math.sin(a) * dy,
            old_m[1] + math.sin(a) * dx + math.cos(a) * dy,
            old_m[2] + da,
        ]
        angle = math.atan2(math.sin(m[2] - expected[2]), math.cos(m[2] - expected[2]))
        if math.hypot(m[0] - expected[0], m[1] - expected[1]) > 0.15 or abs(angle) > 0.15:
            self.failed = True
        return not self.failed
