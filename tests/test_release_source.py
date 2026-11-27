"""Exercise release source identity against real local Git repositories."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def release_source(tmp_path):
    repo = tmp_path / "node"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts/check_release.py",
                 repo / "scripts/check_release.py")
    (repo / "pyproject.toml").write_text(
        '[project]\nversion = "2.0.0"\ndependencies = '
        '["dora-rs==1.0.1", "forge-msgs>=2.1.0,<3", "forge-common>=2.1.0,<3"]\n')
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-b", "main")
    git("config", "user.name", "Release Test")
    git("config", "user.email", "test@example.invalid")
    git("add", ".")
    git("commit", "-m", "test source")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("tag", "-a", "v2.0.0", "-m", "test release")
    return repo, git


def run_check(repo, tag="v2.0.0"):
    return subprocess.run([sys.executable, "scripts/check_release.py", "--tag", tag],
                          cwd=repo, capture_output=True, text=True)


def test_accepts_clean_annotated_release(release_source):
    repo, _ = release_source
    result = run_check(repo)
    assert result.returncode == 0, result.stderr
    assert '"source_commit"' in result.stdout


def test_rejects_dirty_source(release_source):
    repo, _ = release_source
    with (repo / "pyproject.toml").open("a") as stream:
        stream.write("# changed after tag\n")
    result = run_check(repo)
    assert result.returncode != 0 and "dirty" in result.stderr


def test_rejects_lightweight_tag(release_source):
    repo, git = release_source
    git("tag", "-d", "v2.0.0")
    git("tag", "v2.0.0")
    result = run_check(repo)
    assert result.returncode != 0 and "annotated" in result.stderr


def test_rejects_version_mismatch(release_source):
    repo, _ = release_source
    result = run_check(repo, "v2.0.1")
    assert result.returncode != 0 and "source version" in result.stderr


def test_rejects_unmerged_source(release_source):
    repo, git = release_source
    (repo / "extra").write_text("not on main")
    git("add", "extra")
    git("commit", "-m", "unmerged change")
    git("tag", "-d", "v2.0.0")
    git("tag", "-a", "v2.0.0", "-m", "unmerged release")
    assert run_check(repo).returncode != 0


@pytest.mark.parametrize("version", ["0.4.3", "1.0.0", "3.0.0", "2.0.0rc1", "2.0.0-dev", "2.00.0"])
def test_rejects_wrong_release_line_or_nonstable_version(release_source, version):
    repo, git = release_source
    manifest = repo / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('"2.0.0"', f'"{version}"'))
    git("add", "pyproject.toml")
    git("commit", "-m", "change package version")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("tag", "-a", "v" + version, "-m", "test release")
    result = run_check(repo, "v" + version)
    assert result.returncode != 0 and "stable v2.X.Y" in result.stderr
