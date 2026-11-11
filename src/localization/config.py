"""YAML/environment configuration, validated before opening ROS or hardware."""

from __future__ import annotations

import math
import os
from pathlib import Path

import yaml

CONFIG_ENV_VAR = "LOCALIZATION_CONFIG"
REQUIRED = [
    "ros_domain_id",
    "session_dir",
    "map_file",
    "time_mapping_id",
    "odometry_topic",
    "scan_topic",
    "backend_params",
    "launch_file",
]


def load_config(path: str | Path | None = None) -> dict:
    selected = path or os.environ.get(CONFIG_ENV_VAR)
    if not selected:
        raise ValueError(f"use --config or {CONFIG_ENV_VAR}")
    source = Path(selected).expanduser().resolve()
    value = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("configuration must be a mapping")
    missing = [key for key in REQUIRED if key not in value]
    if missing:
        raise ValueError(f"missing configuration fields: {', '.join(missing)}")
    domain = value["ros_domain_id"]
    if isinstance(domain, bool) or not isinstance(domain, int) or not 0 <= domain <= 232:
        raise ValueError("ros_domain_id must be an integer in 0..232")
    for key, item in value.items():
        if key.endswith(("_seconds", "_hz", "_mps", "_radps")):
            if (
                isinstance(item, bool)
                or not isinstance(item, (float, int))
                or not math.isfinite(item)
                or item <= 0
            ):
                raise ValueError(f"{key} must be finite and positive")
        if key.endswith("_topic") and (not isinstance(item, str) or not item.startswith("/")):
            raise ValueError(f"{key} must be an absolute ROS topic")
        if key in {
            "deployment_root",
            "backend_executable",
            "backend_params",
            "session_dir",
            "map_file",
            "pcd_path",
            "launch_file",
            "pose_cache_file",
        }:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{key} must be a path")
            target = Path(os.path.expandvars(item)).expanduser()
            value[key] = str(
                (source.parent / target).resolve() if not target.is_absolute() else target
            )
    return value
