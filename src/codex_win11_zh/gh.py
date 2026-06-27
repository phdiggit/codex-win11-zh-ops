from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .encoding import read_text_auto
from .pr_body import compare_body, validate_file


@dataclass(frozen=True)
class GhResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    def json(self) -> Any:
        return json.loads(self.stdout)


def run_gh(args: list[str], *, cwd: str | Path | None = None) -> GhResult:
    full_args = ["gh", *args]
    proc = subprocess.run(full_args, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    return GhResult(args=full_args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def gh_found() -> bool:
    return shutil.which("gh") is not None


def preflight(*, cwd: str | Path | None = None, hostname: str = "github.com") -> dict[str, Any]:
    result: dict[str, Any] = {
        "gh_found": gh_found(),
        "authenticated": False,
        "hostname": hostname,
        "repo_detected": False,
        "repo": None,
        "default_branch": None,
        "preferred_interface": "connector",
        "fallback_reason": None,
    }
    if not result["gh_found"]:
        result["fallback_reason"] = "gh not found on PATH"
        return result

    auth = run_gh(["auth", "status", "--hostname", hostname], cwd=cwd)
    result["authenticated"] = auth.returncode == 0
    if not result["authenticated"]:
        result["fallback_reason"] = "gh auth status failed"
        result["auth_stderr"] = auth.stderr.strip()
        return result

    repo = run_gh(["repo", "view", "--json", "nameWithOwner,defaultBranchRef"], cwd=cwd)
    if repo.returncode == 0:
        data = repo.json()
        result["repo_detected"] = True
        result["repo"] = data.get("nameWithOwner")
        default_branch = data.get("defaultBranchRef") or {}
        result["default_branch"] = default_branch.get("name")
        result["preferred_interface"] = "gh"
        return result

    result["fallback_reason"] = "gh repo view failed"
    result["repo_stderr"] = repo.stderr.strip()
    return result


def pr_view(pr: str, *, cwd: str | Path | None = None) -> dict[str, Any]:
    fields = "number,url,title,body,baseRefName,headRefName,headRefOid,isDraft,state"
    result = run_gh(["pr", "view", pr, "--json", fields], cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.json()


def pr_create(*, title: str, body_file: str | Path, base: str, head: str, draft: bool = False, cwd: str | Path | None = None) -> dict[str, Any]:
    issues = validate_file(body_file)
    if issues:
        details = "\n".join(f"{i.code}: {i.message}" for i in issues)
        raise ValueError(f"PR body validate failed:\n{details}")

    args = ["pr", "create", "--title", title, "--body-file", str(body_file), "--base", base, "--head", head]
    if draft:
        args.append("--draft")
    result = run_gh(args, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    pr_ref = result.stdout.strip().splitlines()[-1]
    view = pr_view(pr_ref, cwd=cwd)
    verify_pr_view(view, title=title, body_file=body_file, base=base, head=head, draft=draft)
    return view


def pr_edit(*, pr: str, title: str, body_file: str | Path, base: str | None = None, head: str | None = None, draft: bool | None = None, cwd: str | Path | None = None) -> dict[str, Any]:
    issues = validate_file(body_file)
    if issues:
        details = "\n".join(f"{i.code}: {i.message}" for i in issues)
        raise ValueError(f"PR body validate failed:\n{details}")

    args = ["pr", "edit", pr, "--title", title, "--body-file", str(body_file)]
    if base:
        args.extend(["--base", base])
    if draft is False:
        args.append("--ready")
    result = run_gh(args, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    view = pr_view(pr, cwd=cwd)
    verify_pr_view(view, title=title, body_file=body_file, base=base, head=head, draft=draft)
    return view


def verify_pr_view(view: dict[str, Any], *, title: str | None = None, body_file: str | Path | None = None, base: str | None = None, head: str | None = None, draft: bool | None = None) -> None:
    failures: list[str] = []
    if title is not None and view.get("title") != title:
        failures.append(f"title mismatch: expected {title!r}, got {view.get('title')!r}")
    if base is not None and view.get("baseRefName") != base:
        failures.append(f"base mismatch: expected {base!r}, got {view.get('baseRefName')!r}")
    if head is not None and view.get("headRefName") != head:
        failures.append(f"head mismatch: expected {head!r}, got {view.get('headRefName')!r}")
    if draft is not None and bool(view.get("isDraft")) != bool(draft):
        failures.append(f"draft mismatch: expected {draft!r}, got {view.get('isDraft')!r}")
    if body_file is not None:
        local = read_text_auto(body_file).text
        failures.extend(f"{i.code}: {i.message}" for i in compare_body(local, view.get("body") or ""))
    if failures:
        raise ValueError("PR verify failed:\n" + "\n".join(failures))
