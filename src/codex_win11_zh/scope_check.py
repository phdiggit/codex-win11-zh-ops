from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .git import status_short, untracked_files


@dataclass(frozen=True)
class WorkspaceReport:
    clean: bool
    status_short: str
    untracked: str


def check_workspace_clean(*, cwd: str | Path | None = None) -> WorkspaceReport:
    status = status_short(cwd=cwd)
    untracked = untracked_files(cwd=cwd)
    clean = status.returncode == 0 and untracked.returncode == 0 and not status.stdout.strip() and not untracked.stdout.strip()
    return WorkspaceReport(clean=clean, status_short=status.stdout, untracked=untracked.stdout)
