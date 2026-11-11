import json
import os
import sys

from localization.process import OwnedProcess


def test_external_backend_restores_host_libraries(tmp_path, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/private:/host/ros")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/ros")
    target = tmp_path / "environment.json"
    child = OwnedProcess()
    try:
        child.start(
            [
                sys.executable,
                "-c",
                "import json,os,sys;open(sys.argv[1],'w').write(json.dumps(dict(os.environ)))",
                str(target),
            ],
            cwd=tmp_path,
            log_path=tmp_path / "child.log",
        )
        assert child.process.wait(timeout=5) == 0
        assert json.loads(target.read_text())["LD_LIBRARY_PATH"] == "/host/ros"
        assert os.environ["LD_LIBRARY_PATH"] == "/frozen/private:/host/ros"
    finally:
        child.stop()
