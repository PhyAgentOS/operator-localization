import json
import threading

import pytest

from localization import relocalization, view_data


def test_blocked_disk_coalesces_three_streams_without_stalling_producer(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = view_data._write_payload
    written = []

    def blocked(path, payload):
        written.append((path.name, json.loads(payload)))
        if len(written) == 1:
            entered.set()
            assert release.wait(5)
        original(path, payload)

    monkeypatch.setattr(view_data, "_write_payload", blocked)
    writer = view_data.LatestJsonWriter({n: tmp_path / (n + ".json") for n in ("map", "pose", "cache")})
    try:
        writer.submit("pose", {"issued_monotonic": 1, "pose": [0, 0, 0]})
        assert entered.wait(2)
        writer.submit("map", {"issued_monotonic": 2, "data": [0, 100]})
        monkeypatch.setattr(relocalization.time, "time", lambda: 100.)
        pose = [1, 2, .3]
        writer.submit("cache", relocalization.pose_cache_value("map-a", pose))
        pose[0] = 999
        for i in range(3, 100):
            assert writer.submit("pose", {"issued_monotonic": i, "pose": [i, 0, 0]})
        assert len(writer.pending) == 3
        # A delayed write must retain the capture time, not the flush time.
        monkeypatch.setattr(relocalization.time, "time", lambda: 1000.)
    finally:
        release.set()
        writer.close()
    assert [name for name, _ in written] == ["pose.json", "map.json", "cache.json", "pose.json"]
    assert written[-1][1]["issued_monotonic"] == 99
    cache = json.loads((tmp_path / "cache.json").read_text())
    assert cache == {"map_id": "map-a", "pose": [1, 2, .3], "saved_unix": 100.}
    assert relocalization.load_pose_cache(tmp_path / "cache.json", "map-a", now=101) == [1, 2, .3]
    assert relocalization.load_pose_cache(tmp_path / "cache.json", "map-b", now=101) is None
    assert relocalization.load_pose_cache(tmp_path / "cache.json", "map-a", now=99) is None
    assert relocalization.load_pose_cache(tmp_path / "cache.json", "map-a", now=10**9) is None
    assert not list(tmp_path.glob("*.tmp"))


def test_write_failure_preserves_old_record_and_reports(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cache.json"
    view_data.atomic_json(path, {"old": True})
    old = path.read_bytes()

    def fail(*args):
        raise OSError("unavailable")

    monkeypatch.setattr(view_data, "_write_payload", fail)
    writer = view_data.LatestJsonWriter({"cache": path})
    writer.submit("cache", {"old": False})
    writer.close()
    assert path.read_bytes() == old
    assert writer.error == "OSError"
    assert not writer.submit("cache", {})
    assert "Localization records stopped: OSError" in capsys.readouterr().err


def test_stalled_writer_has_bounded_shutdown(tmp_path, monkeypatch, capsys):
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(view_data, "_write_payload", blocked)
    writer = view_data.LatestJsonWriter({"pose": tmp_path / "pose.json"})
    try:
        writer.submit("pose", {})
        assert entered.wait(2)
        writer.close(timeout=.01)
        assert writer.thread.is_alive()
        assert not writer.submit("pose", {})
        assert "shutdown timed out" in capsys.readouterr().err
    finally:
        release.set()
        writer.close()


def test_invalid_record_is_rejected_before_filesystem_changes(tmp_path):
    path = tmp_path / "pose.json"
    view_data.atomic_json(path, {"old": True})
    writer = view_data.LatestJsonWriter({"pose": path})
    try:
        with pytest.raises(ValueError):
            writer.submit("pose", {"x": float("nan")})
        with pytest.raises(ValueError):
            writer.submit("unknown", {})
    finally:
        writer.close()
    assert json.loads(path.read_text()) == {"old": True}


def test_concurrent_writers_publish_complete_json_with_unique_temporaries(tmp_path):
    path = tmp_path / "record.json"
    barrier = threading.Barrier(3)
    errors = []

    def write(value):
        try:
            barrier.wait()
            for _ in range(40):
                view_data.atomic_json(path, {"value": [value] * 200})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(value,)) for value in (1, 2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    while any(thread.is_alive() for thread in threads):
        try:
            value = json.loads(path.read_text())["value"]
            assert value in ([1] * 200, [2] * 200)
        except FileNotFoundError:
            pass
    for thread in threads:
        thread.join()
    assert not errors
    assert not list(tmp_path.glob("*.tmp"))
