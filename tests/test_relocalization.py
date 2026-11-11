import math

import numpy as np
import pytest

from localization.quality import AgreementGate
from localization.relocalization import ScanMatcher, load_pose_cache, save_pose_cache


def ray_scan(grid, pose, resolution=0.1, max_range=10.0):
    points = []
    for a in np.linspace(-math.pi, math.pi, 240, endpoint=False):
        for r in np.arange(0.2, max_range, 0.04):
            x, y = pose[0] + r * math.cos(pose[2] + a), pose[1] + r * math.sin(pose[2] + a)
            ix, iy = math.floor(x / resolution), math.floor(y / resolution)
            if not (0 <= iy < grid.shape[0] and 0 <= ix < grid.shape[1]):
                break
            if grid[iy, ix] >= 65:
                points.append([r * math.cos(a), r * math.sin(a)])
                break
    return np.array(points)


def test_whole_map_search_recovers_translation_and_yaw_with_rotated_map_origin():
    grid = np.zeros((90, 120), dtype=np.int16)
    grid[[0, -1], :] = 100
    grid[:, [0, -1]] = 100
    grid[20:65, 75] = 100
    grid[25, 15:45] = 100
    grid[65:72, 30:45] = 100
    pose = [5.3, 4.1, 0.7]
    origin = [10.0, -5.0, 0.4]
    result = ScanMatcher(grid, 0.1, origin).search(ray_scan(grid, pose), prior=[-100, -100, 0])
    assert result.pose is not None, result
    c, s = math.cos(origin[2]), math.sin(origin[2])
    expected = [
        origin[0] + c * pose[0] - s * pose[1],
        origin[1] + s * pose[0] + c * pose[1],
        pose[2] + origin[2],
    ]
    assert result.pose[:2] == pytest.approx(expected[:2], abs=0.2)
    assert abs(result.pose[2] - expected[2]) < 0.07


def test_duplicate_rooms_are_rejected_even_with_a_plausible_prior():
    room = np.zeros((60, 60), dtype=np.int16)
    room[[0, -1], :] = 100
    room[:, [0, -1]] = 100
    room[10:25, 40] = 100
    grid = np.concatenate([room, room], axis=1)
    pose = [2.1, 3.2, 0.3]
    result = ScanMatcher(grid, 0.1, [0, 0, 0]).search(ray_scan(room, pose, max_range=7), prior=pose)
    assert result.pose is None
    assert result.score - result.runner_up < 0.06


def test_missing_scan_and_expired_search_do_not_return_a_pose():
    grid = np.zeros((30, 30), dtype=np.int16)
    grid[[0, -1], :] = 100
    matcher = ScanMatcher(grid, 0.1, [0, 0, 0])
    assert matcher.search([[1, 2]]).pose is None
    assert matcher.search(np.ones((50, 2)), timeout=0).pose is None


def test_pose_cache_is_only_a_map_bound_recent_hint(tmp_path):
    p = tmp_path / "cache.json"
    save_pose_cache(p, "map-a", [1, 2, 0.3])
    assert load_pose_cache(p, "map-a") == pytest.approx([1, 2, 0.3])
    assert load_pose_cache(p, "map-b") is None
    assert load_pose_cache(p, "map-a", now=10**12) is None
    p.write_text("{bad")
    assert load_pose_cache(p, "map-a") is None


def test_quality_gate_requires_distinct_scans_and_relaxes_only_after_acquisition():
    gate = AgreementGate(dwell=1.0, grace=0.8)
    assert not gate.observe(0.69, 100, [0, 0, 0], now=0, sample_stamp=0)
    assert not gate.observe(0.70, 100, [0, 0, 0], now=1, sample_stamp=1)
    assert not gate.observe(0.70, 100, [0, 0, 0], now=3, sample_stamp=1)
    assert not gate.observe(0.70, 100, [0, 0, 0], now=3.2, sample_stamp=2)
    assert gate.observe(0.70, 100, [0, 0, 0], now=3.4, sample_stamp=3)
    assert gate.observe(0.66, 100, [0, 0, 0], now=3.6, sample_stamp=4)
    assert gate.observe(0.62, 100, [0, 0, 0], now=3.8, sample_stamp=5)
    assert gate.observe(0.72, 100, [0, 0, 0], now=4.3, sample_stamp=6)
    assert gate.observe(0.6, 100, [0, 0, 0], now=4.5, sample_stamp=7)
    assert not gate.observe(0.6, 100, [0, 0, 0], now=5.4, sample_stamp=8)


def test_quality_gate_rejects_large_mismatch_and_pose_drift_immediately():
    gate = AgreementGate(dwell=1.0)
    for t in (0, 0.5, 1.1):
        gate.observe(0.9, 100, [0, 0, 0], now=t, sample_stamp=t)
    assert gate.locked
    assert not gate.observe(0.4, 100, [0, 0, 0], now=1.2, sample_stamp=1.2)
    assert not gate.observe(0.9, 100, [0, 0, 0], now=2, sample_stamp=2)
    assert not gate.observe(0.9, 100, [0.3, 0, 0], now=3.1, sample_stamp=3.1)
    assert not gate.locked
