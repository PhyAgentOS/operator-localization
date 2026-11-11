"""Keep ROS2 Python bindings external to the frozen Forge node.

Source ROS2 and any message overlay before starting the executable. The host
provides ROS middleware and generated messages with the matching Python ABI.
"""

import os
import sys

for directory in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    if directory and directory not in sys.path:
        sys.path.append(directory)
