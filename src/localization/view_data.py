"""Atomic viewer state and portable occupancy-grid image rendering."""

import json
import sys
import tempfile
import threading
from pathlib import Path


def atomic_json(path, value):
    _write_payload(path, json.dumps(value, ensure_ascii=False, allow_nan=False))


def _write_payload(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class LatestJsonWriter:
    """Persist bounded latest records without blocking localization callbacks.

    Callers capture observation timestamps before submission. Serialization also
    happens before enqueueing, so mutable poses and delayed writes cannot renew
    a record's freshness. Filesystem IO never holds the submission lock.
    """

    def __init__(self, paths):
        self.paths = dict(paths)
        self.pending = {}
        self.condition = threading.Condition()
        self.closing = False
        self.error = None
        self.thread = threading.Thread(target=self._run, name="localization-record-writer", daemon=True)
        self.thread.start()

    def submit(self, name, value):
        if name not in self.paths:
            raise ValueError("Record name was not declared")
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self.condition:
            if self.closing or self.error is not None:
                return False
            self.pending[name] = payload
            self.condition.notify()
        return True

    def _run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.pending or self.closing)
                    if not self.pending:
                        return
                    name = next(iter(self.pending))
                    payload = self.pending.pop(name)
                _write_payload(self.paths[name], payload)
        except Exception as exc:
            with self.condition:
                self.error = type(exc).__name__
                self.pending.clear()
            print(f"Localization records stopped: {self.error} ({name})", file=sys.stderr)

    def close(self, timeout=2.0):
        with self.condition:
            self.closing = True
            self.condition.notify()
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            print("Localization record writer shutdown timed out", file=sys.stderr)
