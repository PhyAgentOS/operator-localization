# Third-party dependencies

This repository's adapter code is licensed under Apache-2.0 (see LICENSE).
The frozen Node includes its Python runtime and Python dependencies. Their
installed license files and metadata are retained by PyInstaller hooks where
available; build environments must preserve dependency license information.

ROS2 Jazzy, its middleware and generated message bindings are external runtime
requirements, supplied by the host's ROS installation, not included in this
archive. Source the ROS setup and message overlay before invoking the Node.
FAST-LIO2, SLAM Toolbox, Nav2 and Livox SDK/driver are separately installed
backends, each governed by its upstream license. This archive does not contain
those backend sources or executables and does not grant redistribution rights
for proprietary robot SDKs.

Python: PSF license; NumPy: BSD-3-Clause; PyArrow: Apache-2.0;
PyYAML: MIT; Dora: Apache-2.0. Forge packages retain their distributed licenses.
