"""Materialize optional ROS backend parameter overrides without changing source files."""

import copy
from pathlib import Path

import yaml


def materialize(config):
    if "backend_overrides" not in config:
        return config
    values = yaml.safe_load(Path(config["backend_params"]).read_text())

    def merge(target, patch):
        if not isinstance(patch, dict):
            raise ValueError("backend overrides must be a mapping")
        for key, value in patch.items():
            if isinstance(value, dict):
                child = target.setdefault(key, {})
                if not isinstance(child, dict):
                    raise ValueError("cannot merge a mapping into a scalar")
                merge(child, value)
            else:
                target[key] = copy.deepcopy(value)

    merge(values, config["backend_overrides"])
    folder = Path(config["session_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "localization_backend.yaml"
    target.write_text(yaml.safe_dump(values))
    return {**config, "backend_params": str(target)}
