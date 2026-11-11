"""Command-line entry point; importing this module does not load ROS2."""

from __future__ import annotations

import argparse

from forge_common import get_logger

from . import __version__
from .config import load_config

logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(prog="localization")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", help="YAML file, or LOCALIZATION_CONFIG")
    parser.add_argument(
        "--check-config", action="store_true", help="Validate without connecting hardware"
    )
    parser.add_argument(
        "--check-runtime", action="store_true", help="Check ROS2 imports without hardware"
    )
    args = parser.parse_args()
    try:
        if args.check_runtime:
            from .runtime_check import check_runtime

            check_runtime()
            print("ROS2 runtime valid (no hardware connection)")
            return 0
        config = load_config(args.config)
        if args.check_config:
            print("configuration valid")
            return 0
        from .node import run

        run(config)
        return 0
    except Exception as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
