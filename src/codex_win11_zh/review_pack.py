from __future__ import annotations

import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .encoding import read_text_auto, write_utf8_no_bom
from .gh import pr_body_apply, pr_view, run_gh
from .git import run_git
from .pr_body import normalize_text


DEFAULT_CONFIG = ".codex/review-pack.json"
DEFAULT_APPLY_BODY_FILE = ".tmp/review-pack-pr-body.md"
REVIEW_PACK_SECTION_TITLE = "Codex PR Review Package"
PACKAGE_SECTIONS = [
    "# Codex PR Review Package",
    "## Reviewer Quick Summary",
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
        raise ValueError(format_missing_scope_profile(profile_name, config))
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


def available_scope_profiles(config: dict[str, Any]) -> list[str]:
    profiles = config.get("scope_profiles", {})
    if not isinstance(profiles, dict):
        return []
    return sorted(str(name) for name in profiles)


def format_missing_scope_profile(profile_name: str, config: dict[str, Any]) -> str:
    available = available_scope_profiles(config)
    if available:
        available_text = ", ".join(available)
        suggestion = f"rerun with one of: {available_text}; or omit --scope-profile for fact-only classification"
    else:
        available_text = "none"
        suggestion = "rerun without --scope-profile, or add scope_profiles to .codex/review-pack.json"
    return (
        f"unknown review-pack scope profile: {profile_name}\n"
        f"available profiles: {available_text}\n"
        f"suggestion: {suggestion}"
    )


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
            "validation_summary": summarize_validation_metadata({}),
        }
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"source": str(path), "data": data, "validation_summary": summarize_validation_metadata(data)}


def summarize_validation_metadata(data: dict[str, Any]) -> dict[str, Any]:
    validation = data.get("validation", {}) if isinstance(data, dict) else {}
    if not isinstance(validation, dict):
        validation = {}

    current = _validation_entries(validation.get("current_snapshot", []))
    base = _validation_entries(validation.get("base_snapshot", []))
    historical = _validation_entries(validation.get("historical", []))
    command_entries = _validation_entries(data.get("commands", [])) if isinstance(data, dict) else []
    if not current:
        current = [entry for entry in command_entries if str(entry.get("kind", "")).startswith("current")]

    current_results = _results(current)
    if not current and not command_entries:
        validation_summary = "unknown"
    elif current_results and all(result == "passed" for result in current_results):
        validation_summary = "passed"
    elif "failed" in current_results:
        validation_summary = "failed"
    else:
        validation_summary = "partial"

    current_failed = [entry for entry in current if _entry_result(entry) == "failed"]
    base_failed = [entry for entry in base if _entry_result(entry) == "failed"]
    historical_failed = [entry for entry in historical if _entry_result(entry) == "failed"]

    if current:
        pr_induced = "listed" if current_failed else "none"
    else:
        pr_induced = "unknown"

    if (base_failed or historical_failed) and current_results and "failed" not in current_results:
        fixed_baseline = "listed"
    elif (base or historical) and current_results:
        fixed_baseline = "none"
    else:
        fixed_baseline = "unknown"

    return {
        "validation_summary": validation_summary,
        "pr_induced_failures": pr_induced,
        "pr_induced_failure_items": [_entry_label(entry) for entry in current_failed],
        "fixed_baseline_failures": fixed_baseline,
        "fixed_baseline_failure_items": [_entry_label(entry) for entry in [*base_failed, *historical_failed]],
        "current_snapshot": current,
        "base_snapshot": base,
        "historical": historical,
    }


def _validation_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _entry_result(entry: dict[str, Any]) -> str:
    return str(entry.get("result") or entry.get("status") or "unknown").lower()


def _results(entries: list[dict[str, Any]]) -> list[str]:
    return [_entry_result(entry) for entry in entries if _entry_result(entry) != "unknown"]


def _entry_label(entry: dict[str, Any]) -> str:
    command = entry.get("command") or entry.get("cmd") or entry.get("name") or "unnamed check"
    summary = entry.get("summary")
    if summary:
        return f"{command}: {summary}"
    return str(command)


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
        "quick_summary": build_quick_summary(scope=scope, protocol=protocol, commands=commands),
    }


def build_quick_summary(*, scope: dict[str, Any], protocol: dict[str, Any], commands: dict[str, Any]) -> dict[str, str]:
    head_status_map = {"pass": "current", "fail": "stale", "unknown": "unknown"}
    validation = commands.get("validation_summary", {}) if isinstance(commands, dict) else {}
    return {
        "head_status": head_status_map.get(protocol["head_sha_matches_current_head"]["status"], "unknown"),
        "scope_verdict": str(scope["scope_verdict"]),
        "validation_summary": str(validation.get("validation_summary", "unknown")),
        "pr_induced_failures": str(validation.get("pr_induced_failures", "unknown")),
        "fixed_baseline_failures": str(validation.get("fixed_baseline_failures", "unknown")),
        "merge_judgment": "not_provided_by_tool",
    }


def render_review_pack(data: dict[str, Any]) -> str:
    snapshot = data["snapshot"]
    scope = data["scope"]
    commands = data["commands"]
    protocol = data["protocol"]
    quick_summary = data["quick_summary"]

    lines: list[str] = [
        "# Codex PR Review Package",
        "",
        "## Reviewer Quick Summary",
        "",
        f"- head_status: `{quick_summary['head_status']}`",
        f"- scope_verdict: `{quick_summary['scope_verdict']}`",
        f"- validation_summary: `{quick_summary['validation_summary']}`",
        f"- pr_induced_failures: `{quick_summary['pr_induced_failures']}`",
        f"- fixed_baseline_failures: `{quick_summary['fixed_baseline_failures']}`",
        f"- merge_judgment: `{quick_summary['merge_judgment']}`",
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
            *_validation_summary_lines(commands),
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


def _validation_summary_lines(commands: dict[str, Any]) -> list[str]:
    validation = commands.get("validation_summary", {}) if isinstance(commands, dict) else {}
    if not validation:
        return ["- validation_summary: `unknown`"]
    lines = [
        f"- validation_summary: `{validation.get('validation_summary', 'unknown')}`",
        f"- pr_induced_failures: `{validation.get('pr_induced_failures', 'unknown')}`",
        f"- fixed_baseline_failures: `{validation.get('fixed_baseline_failures', 'unknown')}`",
        "",
        "current_snapshot:",
        *_entry_bullets(validation.get("current_snapshot", [])),
        "",
        "base_snapshot:",
        *_entry_bullets(validation.get("base_snapshot", [])),
        "",
        "historical:",
        *_entry_bullets(validation.get("historical", [])),
    ]
    return lines


def _entry_bullets(entries: Any) -> list[str]:
    if not entries:
        return ["- none"]
    if not isinstance(entries, list):
        return ["- unknown"]
    return [f"- `{_entry_result(entry)}` {_entry_label(entry)}" for entry in entries if isinstance(entry, dict)] or ["- none"]


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
    write_utf8_no_bom(output, normalize_text(markdown))


def splice_review_pack_into_body(body: str, package_text: str, *, section: str = REVIEW_PACK_SECTION_TITLE) -> str:
    normalized_body = normalize_text(body) if body.strip() else ""
    normalized_package = normalize_text(package_text)
    if _heading_bounds(normalized_package, section) is None:
        raise ValueError(f"review package does not contain section marker: {section}")

    bounds = _heading_bounds(normalized_body, section)
    if bounds is None:
        if normalized_body:
            return normalize_text(normalized_body.rstrip("\n") + "\n\n" + normalized_package)
        return normalized_package

    lines = normalized_body.splitlines()
    start, end = bounds
    package_lines = normalized_package.rstrip("\n").splitlines()
    merged = "\n".join([*lines[:start], *package_lines, *lines[end:]])
    return normalize_text(merged)


def _heading_bounds(text: str, section: str) -> tuple[int, int] | None:
    if not text.strip():
        return None
    lines = text.splitlines()
    heading_re = re.compile(rf"^(#{{1,6}})\s+{re.escape(section)}\s*$")
    for index, line in enumerate(lines):
        match = heading_re.match(line.strip())
        if not match:
            continue
        level = len(match.group(1))
        end = len(lines)
        for next_index in range(index + 1, len(lines)):
            next_match = re.match(r"^(#{1,6})\s+", lines[next_index].strip())
            if next_match and len(next_match.group(1)) <= level:
                end = next_index
                break
        return index, end
    return None


def prepare_review_pack_body(
    *,
    pr: str,
    package_file: str | Path,
    body_file: str | Path | None = None,
    section: str = REVIEW_PACK_SECTION_TITLE,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    view = pr_view(pr, cwd=cwd)
    package_text = read_text_auto(package_file).text
    head_sha = str(view.get("headRefOid") or "")
    if head_sha and head_sha not in package_text:
        raise ValueError(f"review package does not contain current head SHA: {head_sha}")
    merged = splice_review_pack_into_body(str(view.get("body") or ""), package_text, section=section)
    output = Path(body_file) if body_file is not None else Path(DEFAULT_APPLY_BODY_FILE)
    write_utf8_no_bom(output, merged)
    return {"body_file": str(output), "head_sha": head_sha, "view": view}


def apply_review_pack_to_pr(
    *,
    pr: str,
    package_file: str | Path,
    body_file: str | Path | None = None,
    section: str = REVIEW_PACK_SECTION_TITLE,
    cwd: str | Path | None = None,
    require_sections: bool = True,
) -> dict[str, Any]:
    prepared = prepare_review_pack_body(pr=pr, package_file=package_file, body_file=body_file, section=section, cwd=cwd)
    view = pr_body_apply(pr=pr, body_file=prepared["body_file"], cwd=cwd, require_sections=require_sections)
    remote_body = str(view.get("body") or "")
    if _heading_bounds(remote_body, section) is None:
        raise ValueError(f"remote PR body does not contain review package marker: {section}")
    head_sha = prepared["head_sha"]
    if head_sha and head_sha not in remote_body:
        raise ValueError(f"remote PR body does not contain current head SHA: {head_sha}")
    return {"body_file": prepared["body_file"], "head_sha": head_sha, "view": view}


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
