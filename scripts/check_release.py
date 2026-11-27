"""Validate an immutable Forge 2 release source before building artifacts."""
import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path


def validate(root, tag):
    if not re.fullmatch(r"v2\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag):
        raise ValueError("Forge 2 releases require a stable v2.X.Y tag")
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    if tag != "v" + project["version"]:
        raise ValueError("Tag must match the source version")
    dependencies = project["dependencies"]
    if "dora-rs==1.0.1" not in dependencies:
        raise ValueError("Release requires the tested Dora 1.0.1 binding")
    for family in ("forge-msgs", "forge-common"):
        if family + ">=2.1.0,<3" not in dependencies:
            raise ValueError("Release requires Forge 2 dependencies")
    return project["version"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = validate(root, args.tag)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    if git("cat-file", "-t", "refs/tags/" + args.tag) != "tag":
        raise ValueError("Release tag must be annotated")
    commit = git("rev-parse", "refs/tags/" + args.tag + "^{commit}")
    if commit != git("rev-parse", "HEAD"):
        raise ValueError("Checkout differs from the selected release tag")
    subprocess.run(["git", "merge-base", "--is-ancestor", commit, "origin/main"],
                   cwd=root, check=True)
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Tracked release source is dirty")
    print(json.dumps({"version": version, "source_commit": commit,
                      "forge_line": "2.x", "dora": "1.0.1"}))
