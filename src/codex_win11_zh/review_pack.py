from __future__ import annotations

import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .gh import run_gh
from .git import run_git


DEFAULT_CONFIG = ".codex/review-pack.json"
PACKAGE_SECTIONS = [
    "# Codex PR Review Package",
    "## HEAD SNAPSHOT LOCK",
    "## Scope / Ownership",
    "## Commands Run",
    "## Protocol Compliance",
    "## Findings",
    "## Failed Checks Classification",
    "## Anti-bloat / Lifecycle Notes",
    "## Codex Fact-layer Verdict",
]


@dataclass(frozen=True)
class ReviewSnapshot:
    pr_number: str
    pr_url: str
    base_branch: str
    head_branch: str
    base_sha: str
    head_sha: str
    fetched_at_utc: str
    fetched_at_local: str
    diff_source: str
    changed_files: list[str]
    pr_body: str


def _utc_and_local_now() -> tuple[str, str]:
    utc_now = datetime.now(timezone.utc)
    return utc_now.isoformat(timespec="seconds"), utc_now.astimezone().isoformat(timespec="seconds")


def _normalize_path(value: str) -> str:
    return value.strip().replace("\\", "/").lstrip("\ufeff")


def _pattern_matches(path: str, pattern: str) -> bool:
    pattern = _normalize_path(pattern)
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return path == pattern.rstrip("/")


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(_pattern_matches(path, pattern) for pattern in patterns)


def load_review_pack_config(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    config_path = Path(path)
    if not config_path.exists():
        return {}
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("review-pack config must be a JSON object")
    return data


def classify_scope(changed_files: list[str], *, config: dict[str, Any], profile_name: str | None) -> dict[str, Any]:
    normalized = [_normalize_path(path) for path in changed_files]
    if not profile_name:
        return {
            "scope_profile": None,
            "in_scope": normalized,
            "suspicious_or_out_of_scope": [],
            "forbidden_hits": [],
            "scope_verdict": "clean",
            "note": "no scope profile supplied; all changed files are listed as in_scope without ownership judgment",
        }

    profiles = config.get("scope_profiles", {})
    if not isinstance(profiles, dict) or profile_name not in profiles:
        raise ValueError(f"unknown review-pack scope profile: {profile_name}")
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise ValueError(f"review-pack scope profile must be an object: {profile_name}")

    allow = _string_list(profile.get("allow", []), "allow")
    suspicious = _string_list(profile.get("suspicious", []), "suspicious")
    forbid = _string_list(profile.get("forbid", []), "forbid")

    in_scope: list[str] = []
    suspicious_hits: list[str] = []
    forbidden_hits: list[str] = []
    for path in normalized:
        if _matches_any(path, forbid):
            forbidden_hits.append(path)
        elif _matches_any(path, suspicious) or (allow and not _matches_any(path, allow)):
            suspicious_hits.append(path)
        else:
            in_scope.append(path)

    if forbidden_hits:
        verdict = "blocked"
    elif suspicious_hits:
        verdict = "suspicious"
    else:
        verdict = "clean"

    return {
        "scope_profile": profile_name,
        "allow": allow,
        "suspicious": suspicious,
        "forbid": forbid,
        "in_scope": in_scope,
        "suspicious_or_out_of_scope": suspicious_hits,
        "forbidden_hits": forbidden_hits,
        "scope_verdict": verdict,
    }


def _string_list(value: Any, label: str) -> list[str]:
    if isinstance(value, str):
        return [_normalize_path(value)]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"review-pack profile field '{label}' must be a string or string list")
    return [_normalize_path(item) for item in value]


def check_pr_body_protocol(body: str, *, head_sha: str, changed_files: list[str]) -> dict[str, Any]:
    changed = [_normalize_path(path) for path in changed_files]
    body_text = body or ""
    head_check = _check_head_sha(body_text, head_sha)
    section_present = _changed_files_section_present(body_text)
    files_check = _check_changed_files(body_text, changed, section_present=section_present)

    if head_check["status"] == "fail" or files_check["status"] == "fail":
        stale = {"status": "fail", "detail": "PR body has mismatched head_sha or changed files"}
    elif head_check["status"] == "pass" and files_check["status"] == "pass":
        stale = {"status": "pass", "detail": "PR body matches current head_sha and changed files"}
    else:
        stale = {"status": "unknown", "detail": "not enough structured PR body data to prove freshness"}

    return {
        "head_sha_matches_current_head": head_check,
        "changed_files_section_present": section_present,
        "changed_files_match_current_diff": files_check,
        "not_obviously_stale": stale,
    }


def _check_head_sha(body: str, head_sha: str) -> dict[str, str]:
    if not head_sha or head_sha == "unknown":
        return {"status": "unknown", "detail": "current head_sha is unknown"}
    if head_sha in body:
        return {"status": "pass", "detail": "current head_sha appears in PR body"}
    head_sha_line = re.search(r"head[_ -]?sha\s*[:：]\s*`?([0-9a-fA-F]{7,40})`?", body, re.IGNORECASE)
    if head_sha_line:
        return {"status": "fail", "detail": f"PR body head_sha is {head_sha_line.group(1)}"}
    return {"status": "unknown", "detail": "no explicit head_sha found in PR body"}


def _changed_files_section_present(body: str) -> dict[str, str]:
    section = _extract_changed_files_section(body)
    if section is None:
        return {"status": "unknown", "detail": "no explicit changed files section found"}
    list_re = re.compile(r"^\s*[-*]\s+.*(`[^`]+`|[/\\]|\.[A-Za-z0-9]+)", re.MULTILINE)
    if list_re.search(section):
        return {"status": "pass", "detail": "changed files or scope section with list found"}
    return {"status": "unknown", "detail": "changed files or scope section found without a file list"}


def _extract_changed_files_section(body: str) -> str | None:
    header_re = re.compile(r"^#{1,4}\s*(范围.*修改文件|修改文件|Changed files|Scope).*", re.IGNORECASE)
    lines = body.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if header_re.match(line):
            start = index + 1
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start, len(lines)):
        if re.match(r"^#{1,4}\s+", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end])


def _check_changed_files(body: str, changed_files: list[str], *, section_present: dict[str, str]) -> dict[str, Any]:
    if section_present["status"] != "pass":
        return {"status": "unknown", "detail": "cannot verify changed files without a changed files section"}
    missing = [path for path in changed_files if path not in body and path.replace("/", "\\") not in body]
    if missing:
        return {"status": "fail", "detail": "PR body is missing changed files", "missing": missing}
    return {"status": "pass", "detail": "all current changed files appear in PR body"}


def load_command_summary(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "source": "current-process",
            "python": sys.version.split()[0],
            "argv_supported": False,
            "note": "no command log supplied; review-pack did not infer validation success",
        }
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"source": str(path), "data": data}


def build_review_pack_data(
    snapshot: ReviewSnapshot,
    *,
    scope: dict[str, Any],
    protocol: dict[str, Any],
    commands: dict[str, Any],
) -> dict[str, Any]:
    return {
        "snapshot": {
            "pr_number": snapshot.pr_number,
            "pr_url": snapshot.pr_url,
            "base_branch": snapshot.base_branch,
            "head_branch": snapshot.head_branch,
            "base_sha": snapshot.base_sha,
            "head_sha": snapshot.head_sha,
            "fetched_at_utc": snapshot.fetched_at_utc,
            "fetched_at_local": snapshot.fetched_at_local,
            "diff_source": snapshot.diff_source,
            "changed_files_count": len(snapshot.changed_files),
            "changed_files": snapshot.changed_files,
        },
        "scope": scope,
        "commands": commands,
        "protocol": protocol,
    }


def render_review_pack(data: dict[str, Any]) -> str:
    snapshot = data["snapshot"]
    scope = data["scope"]
    commands = data["commands"]
    protocol = data["protocol"]

    lines: list[str] = [
        "# Codex PR Review Package",
        "",
        "## HEAD SNAPSHOT LOCK",
        "",
        f"- PR: #{snapshot['pr_number']} {snapshot['pr_url']}",
        f"- base_branch: `{snapshot['base_branch']}`",
        f"- head_branch: `{snapshot['head_branch']}`",
        f"- base_sha: `{snapshot['base_sha']}`",
        f"- head_sha: `{snapshot['head_sha']}`",
        f"- fetched_at_utc: `{snapshot['fetched_at_utc']}`",
        f"- fetched_at_local: `{snapshot['fetched_at_local']}`",
        f"- diff_source: `{snapshot['diff_source']}`",
        f"- changed_files_count: `{snapshot['changed_files_count']}`",
        "",
        "changed_files:",
        *_bullet_list(snapshot["changed_files"]),
        "",
        "## Scope / Ownership",
        "",
        f"- scope_profile: `{scope.get('scope_profile')}`",
        f"- scope_verdict: `{scope['scope_verdict']}`",
    ]
    if scope.get("note"):
        lines.append(f"- note: {scope['note']}")
    lines.extend(
        [
            "",
            "in_scope:",
            *_bullet_list(scope["in_scope"]),
            "",
            "suspicious_or_out_of_scope:",
            *_bullet_list(scope["suspicious_or_out_of_scope"]),
            "",
            "forbidden_hits:",
            *_bullet_list(scope["forbidden_hits"]),
            "",
            "## Commands Run",
            "",
            "```json",
            json.dumps(commands, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Protocol Compliance",
            "",
            *_protocol_lines(protocol),
            "",
            "## Findings",
            "",
            "- Automated fact layer does not create findings. Add human/Codex review findings here.",
            "",
            "## Failed Checks Classification",
            "",
            "- Unknown/unverified checks require reviewer classification.",
            "",
            "## Anti-bloat / Lifecycle Notes",
            "",
            "- Review generated files, ownership boundaries, and lifecycle/deletion expectations manually.",
            "",
            "## Codex Fact-layer Verdict",
            "",
            f"- scope_verdict: `{scope['scope_verdict']}`",
            f"- protocol_stale_status: `{protocol['not_obviously_stale']['status']}`",
            "- merge_judgment: `not_provided_by_tool`",
            "",
        ]
    )
    return "\n".join(lines)


def _bullet_list(items: list[str]) -> list[str]:
    if not items:
        return ["- none"]
    return [f"- `{item}`" for item in items]


def _protocol_lines(protocol: dict[str, Any]) -> list[str]:
    labels = [
        ("head_sha_matches_current_head", "PR body head_sha matches current head"),
        ("changed_files_section_present", "PR body contains final changed files section/list"),
        ("changed_files_match_current_diff", "PR body changed_files matches current diff"),
        ("not_obviously_stale", "PR body is not obviously stale"),
    ]
    lines: list[str] = []
    for key, label in labels:
        item = protocol[key]
        lines.append(f"- {label}: `{item['status']}` - {item['detail']}")
        if item.get("missing"):
            lines.extend(f"  - missing: `{path}`" for path in item["missing"])
    return lines


def collect_review_pack(
    *,
    pr: str,
    base: str,
    scope_profile: str | None = None,
    config_path: str | Path | None = DEFAULT_CONFIG,
    command_log: str | Path | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    view = _fetch_pr_view(pr, cwd=cwd)
    changed_files, diff_source = _fetch_changed_files(pr, base=base, head_sha=view.get("headRefOid") or "HEAD", cwd=cwd)
    base_sha = _resolve_base_sha(base, cwd=cwd)
    utc_now, local_now = _utc_and_local_now()
    snapshot = ReviewSnapshot(
        pr_number=str(view.get("number") or pr),
        pr_url=str(view.get("url") or pr),
        base_branch=base,
        head_branch=str(view.get("headRefName") or "unknown"),
        base_sha=base_sha,
        head_sha=str(view.get("headRefOid") or "unknown"),
        fetched_at_utc=utc_now,
        fetched_at_local=local_now,
        diff_source=diff_source,
        changed_files=changed_files,
        pr_body=str(view.get("body") or ""),
    )
    config = load_review_pack_config(config_path)
    scope = classify_scope(changed_files, config=config, profile_name=scope_profile)
    protocol = check_pr_body_protocol(snapshot.pr_body, head_sha=snapshot.head_sha, changed_files=changed_files)
    commands = load_command_summary(command_log)
    return build_review_pack_data(snapshot, scope=scope, protocol=protocol, commands=commands)


def write_review_pack(path: str | Path, markdown: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")


def _fetch_pr_view(pr: str, *, cwd: str | Path | None = None) -> dict[str, Any]:
    fields = "number,url,title,body,baseRefName,headRefName,headRefOid,state,isDraft"
    result = run_gh(["pr", "view", pr, "--json", fields], cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.json()


def _fetch_changed_files(pr: str, *, base: str, head_sha: str, cwd: str | Path | None = None) -> tuple[list[str], str]:
    gh_result = run_gh(["pr", "diff", pr, "--name-only"], cwd=cwd)
    if gh_result.returncode == 0:
        files = [_normalize_path(line) for line in gh_result.stdout.splitlines() if line.strip()]
        return sorted(files), "gh pr diff --name-only"

    git_result = run_git(["diff", "--name-only", f"{base}...{head_sha}"], cwd=cwd)
    if git_result.returncode != 0:
        raise RuntimeError(gh_result.stderr.strip() or git_result.stderr.strip() or "failed to collect changed files")
    files = [_normalize_path(line) for line in git_result.stdout.splitlines() if line.strip()]
    return sorted(files), f"git diff --name-only {base}...{head_sha}"


def _resolve_base_sha(base: str, *, cwd: str | Path | None = None) -> str:
    for ref in (f"origin/{base}", base):
        result = run_git(["rev-parse", ref], cwd=cwd)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return "unknown"
