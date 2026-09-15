from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SyncRepoReq:
    repo: Path
    skip: bool = False


@dataclass
class SyncRepoRes:
    skipped: bool
    commit: str
    message: str


def sync_repo(req: SyncRepoReq) -> SyncRepoRes:
    commit = _git_output(req.repo, ["rev-parse", "--short", "HEAD"])
    if req.skip:
        return SyncRepoRes(skipped=True, commit=commit, message="SKIP_GIT_PULL=1")

    dirty = _git_output(req.repo, ["status", "--porcelain"])
    if dirty.strip():
        return SyncRepoRes(
            skipped=True,
            commit=commit,
            message="working tree is dirty; skip git pull --ff-only",
        )

    pull = subprocess.run(
        ["git", "-C", str(req.repo), "pull", "--ff-only"],
        check=False,
        capture_output=True,
        text=True,
    )
    commit = _git_output(req.repo, ["rev-parse", "--short", "HEAD"])
    if pull.returncode != 0:
        err = (pull.stderr or pull.stdout or "git pull failed").strip()
        return SyncRepoRes(skipped=True, commit=commit, message=err[:500])
    return SyncRepoRes(skipped=False, commit=commit, message=(pull.stdout or "").strip() or "already up to date")


def _git_output(repo: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return (completed.stdout or "").strip()
