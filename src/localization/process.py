"""Only manage process groups created by this component."""

import os
import signal
import subprocess
import time
from pathlib import Path


class OwnedProcess:
    def __init__(self):
        self.process = None
        self._log = None

    def start(self, argv, *, cwd, log_path, env=None):
        if self.process is not None:
            raise RuntimeError("backend already owned; stop before starting again")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log_path, "ab", buffering=0)
        child_env = dict(os.environ if env is None else env)
        # PyInstaller prepends its private libraries. External ROS executables
        # must use the host libraries and the sourced ROS overlay instead.
        if "LD_LIBRARY_PATH_ORIG" in child_env:
            child_env["LD_LIBRARY_PATH"] = child_env["LD_LIBRARY_PATH_ORIG"]
        elif getattr(__import__("sys"), "frozen", False):
            child_env.pop("LD_LIBRARY_PATH", None)
        try:
            self.process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=child_env,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            self._log.close()
            self._log = None
            raise

    def poll(self):
        return None if self.process is None else self.process.poll()

    def stop(self, *, interrupt_timeout=4):
        process = self.process
        if process is None:
            return
        try:
            for sig, timeout in (
                (signal.SIGINT, interrupt_timeout),
                (signal.SIGTERM, 2),
                (signal.SIGKILL, 2),
            ):
                # The leader may already have exited while one of its children remains.
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                deadline = time.monotonic() + timeout
                alive = True
                while time.monotonic() < deadline:
                    process.poll()  # reap the leader without forgetting its group
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        alive = False
                        break
                    time.sleep(0.02)
                if not alive:
                    break
            process.wait(timeout=2)
        finally:
            self.process = None
            if self._log is not None:
                self._log.close()
                self._log = None
