from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_git(args: list[str], *, cwd: str | Path | None = None) -> CommandResult:
    full_args = ["git", "-c", "core.quotepath=false", *args]
    proc = subprocess.run(full_args, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    return CommandResult(args=full_args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def status_short(*, cwd: str | Path | None = None) -> CommandResult:
    return run_git(["status", "--short"], cwd=cwd)


def diff_name_only(base: str | None = None, *, cwd: str | Path | None = None) -> CommandResult:
    args = ["diff", "--name-only"]
    if base:
        args.append(base)
    return run_git(args, cwd=cwd)


def untracked_files(*, cwd: str | Path | None = None) -> CommandResult:
    return run_git(["ls-files", "--others", "--exclude-standard"], cwd=cwd)


def current_branch(*, cwd: str | Path | None = None) -> str | None:
    result = run_git(["branch", "--show-current"], cwd=cwd)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def origin_default_branch(*, cwd: str | Path | None = None) -> str | None:
    result = run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=cwd)
    if result.returncode != 0:
        return None
    ref = result.stdout.strip()
    prefix = "refs/remotes/origin/"
    return ref[len(prefix):] if ref.startswith(prefix) else ref or None
