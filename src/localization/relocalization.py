"""Bounded, stationary scan-to-map candidate search; AMCL remains the pose owner.

No ROS/SDK imports: candidate generation runs in a worker and never commands motion.
Ambiguous hypotheses are rejected, including plausible but non-unique cached poses.
"""

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from localization.quality import planar_pose
from localization.view_data import atomic_json


def angle_delta(a, b):
    return math.atan2(math.sin(a - b), math.cos(a - b))


def map_identity(path):
    """Tie a cached pose to both YAML and image contents, not a session name."""
    import yaml

    path = Path(path).resolve()
    config = yaml.safe_load(path.read_text())
    image = (path.parent / config["image"]).resolve()
    digest = hashlib.sha256(path.read_bytes() + image.read_bytes()).hexdigest()
    return digest


def load_pose_cache(path, map_id, *, max_age=7 * 86400, now=None):
    try:
        record = json.loads(Path(path).read_text())
        age = (time.time() if now is None else now) - record["saved_unix"]
        if record["map_id"] != map_id or not 0 <= age <= max_age:
            return None
        return planar_pose(record["pose"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_pose_cache(path, map_id, pose):
    atomic_json(
        Path(path), {"map_id": map_id, "pose": planar_pose(pose), "saved_unix": time.time()}
    )


@dataclass
class SearchResult:
    pose: list | None
    score: float = 0.0
    runner_up: float = 0.0
    reason: str = ""
    evaluated: int = 0


class ScanMatcher:
    def __init__(self, grid, resolution, origin):
        self.grid = np.asarray(grid, dtype=np.int16).copy()
        self.resolution = float(resolution)
        self.origin = planar_pose(origin)
        if self.grid.ndim != 2 or self.resolution <= 0:
            raise ValueError("invalid map")
        # Truncated obstacle likelihood field using only numpy (no scipy runtime).
        occupied = self.grid >= 65
        self.field = np.zeros(self.grid.shape, dtype=np.float32)
        h, w = self.grid.shape
        r = math.ceil(0.4 / self.resolution)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                distance = math.hypot(dx, dy) * self.resolution
                if distance > 0.4 or abs(dx) >= w or abs(dy) >= h:
                    continue
                ys, xs = slice(max(0, dy), min(h, h + dy)), slice(max(0, dx), min(w, w + dx))
                source = occupied[max(0, -dy) : min(h, h - dy), max(0, -dx) : min(w, w - dx)]
                np.maximum(
                    self.field[ys, xs],
                    source * math.exp(-0.5 * (distance / 0.15) ** 2),
                    out=self.field[ys, xs],
                )

    def _scores(self, candidates, points):
        # Coordinates are local map meters. A base outside known free space is
        # never a candidate; unobserved/outside endpoints count as mismatches.
        c, s = np.cos(candidates[:, 2]), np.sin(candidates[:, 2])
        x = (
            candidates[:, 0, None]
            + c[:, None] * points[None, :, 0]
            - s[:, None] * points[None, :, 1]
        )
        y = (
            candidates[:, 1, None]
            + s[:, None] * points[None, :, 0]
            + c[:, None] * points[None, :, 1]
        )
        ix, iy = (
            np.floor(x / self.resolution).astype(int),
            np.floor(y / self.resolution).astype(int),
        )
        h, w = self.grid.shape
        valid = (ix >= 0) & (iy >= 0) & (ix < w) & (iy < h)
        values = self.field[np.clip(iy, 0, h - 1), np.clip(ix, 0, w - 1)] * valid
        scores = values.mean(axis=1)
        bx = np.floor(candidates[:, 0] / self.resolution).astype(int)
        by = np.floor(candidates[:, 1] / self.resolution).astype(int)
        free = (bx >= 0) & (by >= 0) & (bx < w) & (by < h)
        free &= self.grid[np.clip(by, 0, h - 1), np.clip(bx, 0, w - 1)] == 0
        return np.where(free, scores, -1.0)

    @staticmethod
    def _distinct(candidates, scores, limit):
        selected = []
        for i in np.argsort(scores)[::-1]:
            if scores[i] < 0:
                break
            p = candidates[i]
            if all(
                math.hypot(p[0] - q[0], p[1] - q[1]) > 0.65 or abs(angle_delta(p[2], q[2])) > 0.35
                for q, _ in selected
            ):
                selected.append((p, float(scores[i])))
                if len(selected) >= limit:
                    break
        return selected

    def search(self, points, *, prior=None, timeout=25.0, min_score=0.65, margin=0.06):
        started = time.monotonic()
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        points = points[np.isfinite(points).all(axis=1)]
        if len(points) < 40:
            return SearchResult(None, reason="有效扫描不足 40 点")
        # Uniform angular sampling is performed by the caller. Bound CPU/memory.
        points = points[np.linspace(0, len(points) - 1, min(180, len(points))).astype(int)]
        a = self.origin[2]
        c, s = math.cos(a), math.sin(a)
        # Candidate coordinates and scan vectors use the map-local axes.
        coarse_points = points[::2]
        step = max(1, round(0.30 / self.resolution))
        yy, xx = np.mgrid[0 : self.grid.shape[0] : step, 0 : self.grid.shape[1] : step]
        free = self.grid[yy, xx] == 0
        translations = np.column_stack(
            [(xx[free] + 0.5) * self.resolution, (yy[free] + 0.5) * self.resolution]
        )
        all_candidates, all_scores, evaluated = [], [], 0
        for yaw in np.arange(-math.pi, math.pi, math.radians(5)):
            if time.monotonic() - started > timeout:
                return SearchResult(
                    None, reason="全图搜索超时，请手动指定初始位置", evaluated=evaluated
                )
            candidates = np.column_stack([translations, np.full(len(translations), yaw)])
            # Avoid large transient arrays on large maps.
            for batch in np.array_split(candidates, max(1, math.ceil(len(candidates) / 512))):
                scores = self._scores(batch, coarse_points)
                keep = np.argsort(scores)[-24:]
                all_candidates.extend(batch[keep])
                all_scores.extend(scores[keep])
                evaluated += len(batch)
        if prior is not None:
            px, py, pa = planar_pose(prior)
            all_candidates.append(
                np.array(
                    [
                        c * (px - self.origin[0]) + s * (py - self.origin[1]),
                        -s * (px - self.origin[0]) + c * (py - self.origin[1]),
                        angle_delta(pa, a),
                    ]
                )
            )
            all_scores.append(float(self._scores(np.array([all_candidates[-1]]), coarse_points)[0]))
        if not all_candidates:
            return SearchResult(None, reason="地图没有可搜索的空闲区域")
        coarse = self._distinct(np.asarray(all_candidates), np.asarray(all_scores), 32)
        refined, refined_scores = [], []
        dx, dy, da = np.meshgrid(
            np.arange(-0.30, 0.301, 0.05),
            np.arange(-0.30, 0.301, 0.05),
            np.deg2rad(np.arange(-5, 5.1, 1)),
            indexing="ij",
        )
        offsets = np.column_stack([dx.ravel(), dy.ravel(), da.ravel()])
        for pose, _ in coarse:
            if time.monotonic() - started > timeout:
                return SearchResult(
                    None, reason="精匹配超时，请手动指定初始位置", evaluated=evaluated
                )
            candidates = offsets + pose
            scores = self._scores(candidates, points)
            i = int(np.argmax(scores))
            refined.append(candidates[i])
            refined_scores.append(scores[i])
            evaluated += len(candidates)
        best = self._distinct(np.asarray(refined), np.asarray(refined_scores), 2)
        if not best:
            return SearchResult(None, reason="没有可用的匹配候选", evaluated=evaluated)
        p, score = best[0]
        runner = best[1][1] if len(best) > 1 else 0.0
        if score < min_score or score - runner < margin:
            return SearchResult(
                None, score, runner, "匹配不足或存在多个相似位置，请手动指定初始位置", evaluated
            )
        pose = [
            self.origin[0] + c * p[0] - s * p[1],
            self.origin[1] + s * p[0] + c * p[1],
            angle_delta(p[2] + a, 0),
        ]
        return SearchResult(pose, score, runner, "找到唯一候选，等待 AMCL 收敛校核", evaluated)
