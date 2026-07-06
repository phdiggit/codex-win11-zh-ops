from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .encoding import write_json_utf8
from .runtime import build_runtime_env
from .timing import command_to_text, duration_sec, utc_now_iso


STATUS_FILE = "status.json"
TASKS_FILE = "tasks.jsonl"
CHILDREN_FILE = "children.jsonl"
RESULTS_FILE = "results.jsonl"
SUMMARY_FILE = "summary.json"
CONFIG_FILE = "run_config.json"
TERMINAL_RUN_STATUSES = {"planned", "succeeded", "failed", "killed", "stale_cleaned"}
TERMINAL_TASK_STATUSES = {"planned", "succeeded", "failed", "timed_out", "skipped", "killed", "stale_cleaned"}
SANDBOX_PROFILES = {"read-only", "local-write", "bypass"}
PERMISSION_PROFILES = {"review-only", "tmp-jsonl-review", "local-write", "repo-editor", "bypass"}
DENY_POLICIES = {"fail", "continue-with-final", "deny-fail", "deny-continue", "deny-rewrite"}
DENY_POLICY_ALIASES = {
    "fail": "deny-fail",
    "deny_fail": "deny-fail",
    "deny-fail": "deny-fail",
    "continue-with-final": "deny-rewrite",
    "continue_with_final": "deny-rewrite",
    "deny_continue": "deny-continue",
    "deny-continue": "deny-continue",
    "deny_rewrite": "deny-rewrite",
    "deny-rewrite": "deny-rewrite",
}
PROFILE_SANDBOX = {
    "review-only": "read-only",
    "tmp-jsonl-review": "local-write",
    "local-write": "local-write",
    "repo-editor": "local-write",
    "bypass": "bypass",
}
PROFILE_DENIED_COMMANDS = {
    "review-only": ["git", "pytest", "psql", "python"],
    "tmp-jsonl-review": ["git", "pytest", "psql", "apply --execute", "--execute"],
    "local-write": ["git reset", "git checkout", "git clean"],
    "repo-editor": ["git reset", "git checkout", "git clean", "psql", "apply --execute"],
    "bypass": [],
}
PROFILE_ALLOWED_COMMANDS = {
    "review-only": [],
    "tmp-jsonl-review": [],
    "local-write": ["python", "pwsh", "powershell", "cmd", "git status", "git diff"],
    "repo-editor": ["python", "pwsh", "powershell", "cmd", "git status", "git diff", "pytest"],
    "bypass": ["*"],
}
PROFILE_CAPABILITIES = {
    "review-only": {"network": False, "database": False, "git_read": False, "git_write": False},
    "tmp-jsonl-review": {"network": False, "database": False, "git_read": False, "git_write": False},
    "local-write": {"network": False, "database": False, "git_read": True, "git_write": False},
    "repo-editor": {"network": False, "database": False, "git_read": True, "git_write": False},
    "bypass": {"network": True, "database": True, "git_read": True, "git_write": True},
}
_BACKGROUND_PROCS: list[subprocess.Popen[str]] = []
_JSONL_LOCKS: dict[str, threading.Lock] = {}
EVENT_FAILURE_TYPES = {"error", "turn.failed"}
USAGE_FAILURE_WORDS = ("usage limit", "rate limit", "quota", "auth", "authentication", "unauthorized", "forbidden", "credit")
POLICY_FAILURE_WORDS = ("policy", "denied", "not permitted", "permission", "拒绝")
GIT_WRITE_RE = re.compile(r"\bgit\s+(?:add|am|apply|bisect|branch|checkout|cherry-pick|clean|commit|merge|mv|pull|push|rebase|reset|restore|revert|rm|stash|switch|tag|worktree)\b", re.IGNORECASE)
GIT_READ_RE = re.compile(r"\bgit\s+(?:status|diff|show|log|rev-parse|ls-files|grep|branch)\b", re.IGNORECASE)
DB_COMMAND_RE = re.compile(r"\b(?:psql|mysql|sqlite3|sqlcmd|pg_dump|pg_restore)\b", re.IGNORECASE)
NETWORK_COMMAND_RE = re.compile(r"\b(?:curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm|gh\s+api)\b", re.IGNORECASE)


class AgentRunError(RuntimeError):
    pass


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source = Path(path)
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise AgentRunError(f"expected JSON object at {source}:{line_number}")
        rows.append(value)
    return rows


def write_jsonl(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def append_jsonl_safe(path: str | Path, row: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    key = str(output.resolve())
    lock = _JSONL_LOCKS.setdefault(key, threading.Lock())
    text = json.dumps(dict(row), ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
    with lock:
        for attempt in range(8):
            try:
                with output.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                return
            except OSError:
                if attempt == 7:
                    raise
                time.sleep(0.02 * (attempt + 1))


def read_status(output_root: str | Path) -> dict[str, Any]:
    status_path = Path(output_root) / STATUS_FILE
    if not status_path.exists():
        raise AgentRunError(f"agent run status missing: {status_path}")
    data = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AgentRunError(f"agent run status must be a JSON object: {status_path}")
    return data


def run_plan(
    *,
    tasks_jsonl: str | Path,
    output_root: str | Path,
    cwd: str | Path | None = None,
    max_workers: int = 1,
    timeout_seconds: int = 1800,
    sandbox_profile: str = "read-only",
    codex_bin: str = "codex",
    background: bool = False,
    dry_run: bool = False,
    respect_task_argv: bool = False,
    search: bool = False,
    heartbeat_seconds: float = 1.0,
    permission_profile: str | None = None,
    deny_policy: str = "fail",
    write_roots: Sequence[str | Path] = (),
) -> dict[str, Any]:
    config = {
        "tasks_jsonl": str(Path(tasks_jsonl)),
        "output_root": str(Path(output_root)),
        "cwd": str(Path(cwd)) if cwd is not None else None,
        "max_workers": int(max_workers),
        "timeout_seconds": int(timeout_seconds),
        "sandbox_profile": sandbox_profile,
        "codex_bin": resolve_executable(codex_bin, cwd=Path(cwd or os.getcwd())),
        "dry_run": bool(dry_run),
        "respect_task_argv": bool(respect_task_argv),
        "search": bool(search),
        "heartbeat_seconds": float(heartbeat_seconds),
        "permission_profile": permission_profile,
        "deny_policy": deny_policy,
        "write_roots": [str(Path(path)) for path in write_roots],
    }
    if background and not dry_run:
        return start_background_run(config)
    return run_plan_foreground(config, launched_in_background=False)


def start_background_run(config: Mapping[str, Any]) -> dict[str, Any]:
    output_root = Path(str(config["output_root"])).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    _reset_run_logs(output_root, keep_children=False)
    config_path = output_root / CONFIG_FILE
    write_json_utf8(config_path, dict(config), sort_keys=True)
    initial_status = {
        "schema": "codex-win.agent.status.v1",
        "run_id": uuid.uuid4().hex,
        "status": "starting",
        "started_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "output_root": str(output_root),
        "supervisor_pid": None,
        "background": True,
        "files": _run_files(output_root),
    }
    _write_json_atomic(output_root / STATUS_FILE, initial_status)

    stdout_path = output_root / "supervisor.stdout.log"
    stderr_path = output_root / "supervisor.stderr.log"
    command = [sys.executable, "-m", "codex_win11_zh.agent_worker", "--config", str(config_path)]
    cwd = Path(str(config.get("cwd") or os.getcwd())).resolve()
    env = build_runtime_env()
    src_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with stdout_path.open("a", encoding="utf-8", newline="\n") as stdout, stderr_path.open("a", encoding="utf-8", newline="\n") as stderr:
        proc = subprocess.Popen(command, cwd=str(cwd), stdout=stdout, stderr=stderr, env=env, **subprocess_startup_kwargs(detached=True))
    _BACKGROUND_PROCS.append(proc)

    append_jsonl_safe(output_root / CHILDREN_FILE, {"event": "supervisor_started", "pid": proc.pid, "at": utc_now_iso(), "command": command_to_text(command)})
    initial_status["supervisor_pid"] = proc.pid
    initial_status["command"] = command_to_text(command)
    return initial_status


def subprocess_startup_kwargs(*, detached: bool) -> dict[str, Any]:
    if os.name != "nt":
        return {"start_new_session": True}
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if detached:
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {"creationflags": creationflags, "startupinfo": startupinfo}


def run_plan_foreground(config: Mapping[str, Any], *, launched_in_background: bool) -> dict[str, Any]:
    tasks_path = Path(str(config["tasks_jsonl"])).resolve()
    output_root = Path(str(config["output_root"])).resolve()
    cwd = Path(str(config.get("cwd") or os.getcwd())).resolve()
    max_workers = max(1, int(config.get("max_workers") or 1))
    timeout_seconds = max(1, int(config.get("timeout_seconds") or 1))
    sandbox_profile = str(config.get("sandbox_profile") or "read-only")
    if sandbox_profile not in SANDBOX_PROFILES:
        raise AgentRunError(f"unsupported sandbox profile: {sandbox_profile}")
    permission_profile = _optional_str(config.get("permission_profile"))
    if permission_profile is not None and permission_profile not in PERMISSION_PROFILES:
        raise AgentRunError(f"unsupported permission profile: {permission_profile}")
    deny_policy = normalize_deny_policy(config.get("deny_policy") or "fail")
    heartbeat_seconds = max(0.2, float(config.get("heartbeat_seconds") or 1.0))
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "logs").mkdir(parents=True, exist_ok=True)
    _reset_run_logs(output_root, keep_children=launched_in_background)

    raw_tasks = load_jsonl(tasks_path)
    tasks = normalize_tasks(
        raw_tasks,
        cwd=cwd,
        output_root=output_root,
        permission_profile=permission_profile,
        deny_policy=deny_policy,
        write_roots=[str(path) for path in config.get("write_roots") or []],
    )
    write_jsonl(output_root / TASKS_FILE, tasks)

    state = AgentRunState(
        output_root=output_root,
        tasks=tasks,
        max_workers=max_workers,
        timeout_seconds=timeout_seconds,
        sandbox_profile=sandbox_profile,
        background=launched_in_background,
    )
    state.write(status="running" if not config.get("dry_run") else "planned")
    append_jsonl_safe(output_root / CHILDREN_FILE, {"event": "supervisor_active", "pid": os.getpid(), "at": utc_now_iso()})

    if bool(config.get("dry_run")):
        for task in tasks:
            state.update_task(str(task["task_code"]), status="planned", updated_at=utc_now_iso())
            append_jsonl_safe(output_root / RESULTS_FILE, {"task_code": task["task_code"], "status": "planned"})
        return _finish_run(state, "planned")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                run_one_task,
                task,
                cwd=cwd,
                output_root=output_root,
                state=state,
                timeout_seconds=timeout_seconds,
                sandbox_profile=sandbox_profile,
                codex_bin=str(config.get("codex_bin") or "codex"),
                respect_task_argv=bool(config.get("respect_task_argv")),
                search=bool(config.get("search")),
                heartbeat_seconds=heartbeat_seconds,
            ): task
            for task in tasks
        }
        for future in as_completed(future_map):
            task = future_map[future]
            try:
                future.result()
            except Exception as exc:  # pragma: no cover - defensive guard around worker threads.
                task_code = str(task["task_code"])
                result = {"task_code": task_code, "status": "failed", "error": str(exc), "finished_at": utc_now_iso()}
                state.append_result(result)
                state.update_task(task_code, status="failed", error=str(exc), finished_at=result["finished_at"])

    final_status = "succeeded"
    totals = Counter(row.get("status") for row in state.task_records.values())
    if totals.get("failed") or totals.get("timed_out"):
        final_status = "failed"
    return _finish_run(state, final_status)


def normalize_tasks(
    tasks: Sequence[Mapping[str, Any]],
    *,
    cwd: Path,
    output_root: Path,
    permission_profile: str | None = None,
    deny_policy: str = "fail",
    write_roots: Sequence[str] = (),
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    context_snapshot = build_context_snapshot(cwd)
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, Mapping):
            raise AgentRunError(f"task #{index} is not an object")
        row = dict(task)
        task_code = str(row.get("task_code") or f"task_{index:04d}").strip()
        if not task_code:
            raise AgentRunError(f"task #{index} has an empty task_code")
        if task_code in seen:
            raise AgentRunError(f"duplicate task_code: {task_code}")
        seen.add(task_code)
        prompt_path = _resolve_existing_or_declared_path(row.get("prompt_path"), cwd)
        if prompt_path is None:
            raise AgentRunError(f"task {task_code} is missing prompt_path")
        last_message_path = _resolve_existing_or_declared_path(row.get("last_message_path"), cwd) or (output_root / "logs" / f"{task_code}.last.md")
        event_log_path = _resolve_existing_or_declared_path(row.get("log_path"), cwd) or (output_root / "logs" / f"{task_code}.events.jsonl")
        patch_path = _resolve_existing_or_declared_path(row.get("patch_path"), cwd)
        expected_output_path = _resolve_existing_or_declared_path(row.get("expected_output_path"), cwd)
        expected_outputs = normalize_expected_outputs(row.get("expected_outputs"), cwd=cwd)
        row["task_code"] = task_code
        row["_codex_win"] = {
            "task_index": index,
            "prompt_path": str(prompt_path),
            "patch_path": str(patch_path) if patch_path is not None else None,
            "expected_output_path": str(expected_output_path) if expected_output_path is not None else None,
            "expected_outputs": expected_outputs,
            "last_message_path": str(last_message_path),
            "event_log_path": str(event_log_path),
            "stdout_path": str(event_log_path),
            "stderr_path": str(output_root / "logs" / f"{task_code}.stderr.log"),
        }
        row["_codex_win"]["permission"] = build_permission_record(
            row,
            cwd=cwd,
            output_root=output_root,
            permission_profile=permission_profile,
            deny_policy=deny_policy,
            write_roots=write_roots,
            context_snapshot=context_snapshot,
        )
        normalized.append(row)
    return normalized


def normalize_expected_outputs(value: Any, *, cwd: Path) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise AgentRunError("expected_outputs must be an array of objects")
    outputs: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise AgentRunError(f"expected_outputs[{index}] must be an object")
        row = dict(item)
        kind = str(row.get("kind") or "").strip()
        if kind != "jsonl_patch":
            raise AgentRunError(f"unsupported expected output kind: {kind or '<empty>'}")
        path = _resolve_existing_or_declared_path(row.get("path"), cwd)
        if path is None:
            raise AgentRunError(f"expected_outputs[{index}] is missing path")
        row["kind"] = kind
        row["path"] = str(path)
        row.setdefault("fallback", "none")
        outputs.append(row)
    return outputs


def build_permission_record(
    task: Mapping[str, Any],
    *,
    cwd: Path,
    output_root: Path,
    permission_profile: str | None,
    deny_policy: str,
    write_roots: Sequence[str],
    context_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    profile = _optional_str(task.get("permission_profile")) or permission_profile
    if profile is not None and profile not in PERMISSION_PROFILES:
        raise AgentRunError(f"unsupported permission profile: {profile}")
    task_deny_policy = normalize_deny_policy(task.get("deny_policy") or deny_policy or "fail")
    denied_commands = list(PROFILE_DENIED_COMMANDS.get(profile or "", []))
    denied_commands.extend(_string_list(task.get("denied_commands")))
    allowed_commands = list(PROFILE_ALLOWED_COMMANDS.get(profile or "", []))
    allowed_commands.extend(_string_list(task.get("allowed_commands")))
    capabilities = dict(PROFILE_CAPABILITIES.get(profile or "", {}))
    allowed_write_dirs = default_write_dirs_for_profile(profile, cwd=cwd, output_root=output_root)
    allowed_write_dirs.extend(resolve_path_list(write_roots, cwd=cwd))
    allowed_write_dirs.extend(resolve_path_list(_string_list(task.get("allowed_write_paths")), cwd=cwd))
    deduped_write_dirs = list(dict.fromkeys(str(path) for path in allowed_write_dirs))
    readonly_equivalents = build_readonly_equivalents(
        profile=profile,
        denied_commands=denied_commands,
        capabilities=capabilities,
        context_snapshot=context_snapshot,
    )
    return {
        "profile": profile,
        "deny_policy": task_deny_policy,
        "sandbox_profile": PROFILE_SANDBOX.get(profile or ""),
        "allowed_write_dirs": deduped_write_dirs,
        "allowed_commands": list(dict.fromkeys(command for command in allowed_commands if command)),
        "denied_commands": list(dict.fromkeys(command for command in denied_commands if command)),
        "capabilities": capabilities,
        "readonly_equivalents": readonly_equivalents,
    }


def default_write_dirs_for_profile(profile: str | None, *, cwd: Path, output_root: Path) -> list[Path]:
    if profile == "tmp-jsonl-review":
        return [cwd / "tmp", output_root]
    if profile in {"local-write", "repo-editor", "bypass"}:
        return [cwd / "tmp"]
    return [output_root] if profile == "review-only" else []


def resolve_path_list(values: Sequence[str], *, cwd: Path) -> list[Path]:
    resolved: list[Path] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        path = Path(text)
        resolved.append(path if path.is_absolute() else (cwd / path))
    return resolved


def build_context_snapshot(cwd: Path) -> dict[str, Any]:
    git_context = collect_git_context_snapshot(cwd)
    return {
        "git_context": git_context,
        "git_status": git_context.get("status", {}),
    }


def collect_git_context_snapshot(cwd: Path) -> dict[str, Any]:
    status = run_git_snapshot(cwd, ["status", "--short", "--branch"], max_lines=80)
    branch = run_git_snapshot(cwd, ["branch", "--show-current"], max_lines=1)
    head = run_git_snapshot(cwd, ["rev-parse", "HEAD"], max_lines=1)
    root = run_git_snapshot(cwd, ["rev-parse", "--show-toplevel"], max_lines=1)
    diff_stat = run_git_snapshot(cwd, ["diff", "--stat"], max_lines=80)
    changed_files = run_git_snapshot(cwd, ["diff", "--name-status"], max_lines=120)
    ok = bool(status.get("ok"))
    return {
        "ok": ok,
        "status": status,
        "branch": first_stdout_line(branch),
        "head": first_stdout_line(head),
        "repo_root": first_stdout_line(root),
        "diff_stat": diff_stat,
        "changed_files": changed_files,
    }


def run_git_snapshot(cwd: Path, args: Sequence[str], *, max_lines: int) -> dict[str, Any]:
    command = ["git", "-c", "core.quotepath=false", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            env=build_runtime_env(),
            **subprocess_startup_kwargs(detached=False),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "command": command_to_text(command), "error": str(exc)[:500]}
    stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return {
        "ok": completed.returncode == 0,
        "command": command_to_text(command),
        "returncode": completed.returncode,
        "stdout_lines": stdout_lines[:max_lines],
        "stderr": completed.stderr.strip()[:1000],
        "truncated": len(stdout_lines) > max_lines,
    }


def first_stdout_line(snapshot: Mapping[str, Any]) -> str:
    lines = snapshot.get("stdout_lines") if isinstance(snapshot.get("stdout_lines"), Sequence) and not isinstance(snapshot.get("stdout_lines"), (str, bytes)) else []
    return str(lines[0]) if lines else ""


def build_readonly_equivalents(
    *,
    profile: str | None,
    denied_commands: Sequence[str],
    capabilities: Mapping[str, Any],
    context_snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    equivalents: list[dict[str, Any]] = []
    if not profile:
        return equivalents
    denied_git = any(str(command).strip().lower() in {"git", "git status"} for command in denied_commands)
    if denied_git and not bool(capabilities.get("git_read")):
        git_context = context_snapshot.get("git_context") if isinstance(context_snapshot.get("git_context"), Mapping) else {"ok": False}
        git_status = context_snapshot.get("git_status") if isinstance(context_snapshot.get("git_status"), Mapping) else {"ok": False}
        equivalents.append(
            {
                "command": "git status",
                "replacement": "git_context_snapshot",
                "source": "supervisor",
                "status": "available" if git_context.get("ok") else "unavailable",
                "snapshot": dict(git_context),
                "git_status_snapshot": dict(git_status),
            }
        )
    return equivalents


def normalize_deny_policy(value: Any) -> str:
    policy = str(value or "fail").strip()
    normalized = DENY_POLICY_ALIASES.get(policy)
    if not normalized:
        raise AgentRunError(f"unsupported deny policy: {policy}")
    return normalized


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    return [str(value)]


def run_one_task(
    task: Mapping[str, Any],
    *,
    cwd: Path,
    output_root: Path,
    state: "AgentRunState",
    timeout_seconds: int,
    sandbox_profile: str,
    codex_bin: str,
    respect_task_argv: bool,
    search: bool,
    heartbeat_seconds: float,
) -> dict[str, Any]:
    task_code = str(task["task_code"])
    paths = task["_codex_win"] if isinstance(task.get("_codex_win"), Mapping) else {}
    prompt_path = Path(str(paths.get("prompt_path")))
    stdout_path = Path(str(paths.get("stdout_path")))
    stderr_path = Path(str(paths.get("stderr_path")))
    last_message_path = Path(str(paths.get("last_message_path")))
    for path in (stdout_path, stderr_path, last_message_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    if not prompt_path.exists():
        result = {"task_code": task_code, "status": "failed", "error": f"prompt missing: {prompt_path}", "finished_at": utc_now_iso()}
        state.append_result(result)
        state.update_task(task_code, status="failed", error=result["error"], finished_at=result["finished_at"])
        return result
    permission = task_permission(task)
    task_sandbox_profile = effective_task_sandbox(task, default_sandbox=sandbox_profile)
    preflight = validate_permission_preflight(task, permission=permission)
    if preflight.get("failed"):
        finished_at = utc_now_iso()
        result = {
            "task_code": task_code,
            "status": "failed",
            "error_type": preflight.get("error_type"),
            "error": preflight.get("error_summary"),
            "finished_at": finished_at,
            "paths": dict(paths),
            "permission": permission,
            "permission_analysis": preflight,
        }
        state.append_result(result)
        state.update_task(task_code, status="failed", error_type=result["error_type"], error=result["error"], finished_at=finished_at, permission=permission)
        return result
    prompt_text = inject_permission_prelude(prompt_path.read_text(encoding="utf-8"), task=task, cwd=cwd)
    prompt_bytes = len(prompt_text.encode("utf-8"))

    timeout = int(task.get("timeout_seconds") or timeout_seconds)
    command = build_task_command(
        task,
        cwd=cwd,
        codex_bin=codex_bin,
        sandbox_profile=task_sandbox_profile,
        respect_task_argv=respect_task_argv,
        search=search,
        allowed_write_dirs=[Path(path) for path in permission.get("allowed_write_dirs") or []],
        enforce_permission_sandbox=bool(permission.get("profile")),
    )
    command_info = build_command_info(task, command=command, sandbox_profile=task_sandbox_profile, respect_task_argv=respect_task_argv)
    started_at = utc_now_iso()
    started = time.perf_counter()
    stdout_file = stdout_path.open("w", encoding="utf-8", newline="\n")
    stderr_file = stderr_path.open("w", encoding="utf-8", newline="\n")
    proc: subprocess.Popen[str] | None = None
    stdin_writer: threading.Thread | None = None
    stdin_status: dict[str, Any] = {"attempted": True, "written": False, "bytes": 0}
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=build_runtime_env(),
            **subprocess_startup_kwargs(detached=False),
        )
        state.update_task(
            task_code,
            status="running",
            pid=proc.pid,
            started_at=started_at,
            last_seen_at=started_at,
            command=command_to_text(command),
            prompt_path=str(prompt_path),
            prompt_bytes=prompt_bytes,
            stdin=dict(stdin_status),
            command_info=command_info,
            permission=permission,
        )
        append_jsonl_safe(
            output_root / CHILDREN_FILE,
            {"event": "task_started", "task_code": task_code, "pid": proc.pid, "at": started_at, "command": command_to_text(command)},
        )
        stdin_writer = start_stdin_writer(proc, prompt_text, stdin_status)
        deadline = time.perf_counter() + timeout
        next_heartbeat = time.perf_counter() + heartbeat_seconds
        timed_out = False
        while True:
            returncode = proc.poll()
            if returncode is not None:
                break
            now = time.perf_counter()
            if now >= deadline:
                timed_out = True
                kill_process_tree(proc.pid)
                returncode = proc.wait(timeout=5)
                break
            if now >= next_heartbeat:
                state.update_task(task_code, status="running", pid=proc.pid, last_seen_at=utc_now_iso())
                next_heartbeat = now + heartbeat_seconds
            time.sleep(0.1)
    except subprocess.TimeoutExpired:
        timed_out = True
        if proc is not None:
            kill_process_tree(proc.pid)
            returncode = proc.wait(timeout=5)
        else:
            returncode = -1
    except Exception as exc:
        if proc is not None and proc.poll() is None:
            kill_process_tree(proc.pid)
        result = {"task_code": task_code, "status": "failed", "error": str(exc), "finished_at": utc_now_iso()}
        state.append_result(result)
        state.update_task(task_code, status="failed", error=str(exc), finished_at=result["finished_at"])
        return result
    finally:
        if stdin_writer is not None:
            stdin_writer.join(timeout=1)
        stdout_file.close()
        stderr_file.close()

    finished_at = utc_now_iso()
    elapsed = duration_sec(started, time.perf_counter())
    event_analysis = analyze_codex_events(stdout_path)
    output_analysis = check_task_outputs(task, paths, last_message_path=last_message_path)
    process_analysis = analyze_process_text(stderr_path, last_message_path)
    permission_analysis = analyze_permission_output(task, stdout_path, stderr_path, last_message_path)
    deny_resolution = resolve_deny_policy(
        permission=permission,
        process_analysis=process_analysis,
        permission_analysis=permission_analysis,
        output_analysis=output_analysis,
    )
    process_analysis = deny_resolution["process_analysis"]
    permission_analysis = deny_resolution["permission_analysis"]
    status = "timed_out" if timed_out else ("succeeded" if int(returncode) == 0 else "failed")
    failure: dict[str, Any] | None = None
    if timed_out:
        failure = {"type": "timeout", "message": f"task exceeded timeout_seconds={timeout}"}
    elif int(returncode) != 0:
        failure = {"type": "returncode", "message": f"codex process exited with returncode {int(returncode)}"}
    elif event_analysis.get("failed"):
        failure = {"type": event_analysis.get("error_type") or "codex_event_failed", "message": event_analysis.get("error_summary") or "Codex event log reported failure"}
    elif process_analysis.get("failed"):
        failure = {"type": process_analysis.get("error_type") or "process_output_failed", "message": process_analysis.get("error_summary") or "process output reported failure"}
    elif permission_analysis.get("failed"):
        failure = {"type": permission_analysis.get("error_type") or "permission_policy_failed", "message": permission_analysis.get("error_summary") or "permission policy reported failure"}
    elif output_analysis.get("failed"):
        failure = {"type": output_analysis.get("error_type") or "missing_expected_output", "message": output_analysis.get("error_summary") or "expected output contract was not satisfied"}
    if failure is not None and status != "timed_out":
        status = "failed"
    result = {
        "task_code": task_code,
        "status": status,
        "returncode": int(returncode),
        "timed_out": timed_out,
        "pid": proc.pid if proc is not None else None,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": elapsed,
        "timeout_seconds": timeout,
        "paths": dict(paths),
        "prompt_path": str(prompt_path),
        "prompt_bytes": prompt_bytes,
        "stdin": dict(stdin_status),
        "command_info": command_info,
        "permission": permission,
        "usage": event_analysis.get("usage", {}),
        "event_analysis": event_analysis,
        "output_analysis": output_analysis,
        "process_analysis": process_analysis,
        "permission_analysis": permission_analysis,
        "deny_resolution": deny_resolution["resolution"],
    }
    if failure is not None:
        result["error_type"] = failure["type"]
        result["error"] = failure["message"]
    state.append_result(result)
    state.update_task(
        task_code,
        status=status,
        returncode=int(returncode),
        timed_out=timed_out,
        error_type=result.get("error_type"),
        error=result.get("error"),
        finished_at=finished_at,
        last_seen_at=finished_at,
        duration_sec=elapsed,
        prompt_path=str(prompt_path),
        prompt_bytes=prompt_bytes,
        stdin=dict(stdin_status),
        command_info=command_info,
        permission=permission,
        output_analysis=output_analysis,
    )
    append_jsonl_safe(output_root / CHILDREN_FILE, {"event": "task_finished", "task_code": task_code, "pid": result["pid"], "at": finished_at, "status": status})
    return result


def build_task_command(
    task: Mapping[str, Any],
    *,
    cwd: Path,
    codex_bin: str,
    sandbox_profile: str,
    respect_task_argv: bool,
    search: bool,
    allowed_write_dirs: Sequence[Path] = (),
    enforce_permission_sandbox: bool = False,
) -> list[str]:
    paths = task["_codex_win"] if isinstance(task.get("_codex_win"), Mapping) else {}
    last_message_path = str(paths.get("last_message_path"))
    if respect_task_argv:
        argv = [str(part) for part in task.get("argv") or []]
        if not argv:
            raise AgentRunError(f"task {task.get('task_code')} has no argv")
        argv[0] = resolve_executable(argv[0], cwd=cwd)
        return normalize_respected_codex_argv(
            argv,
            cwd=cwd,
            sandbox_profile=sandbox_profile,
            last_message_path=last_message_path,
            allowed_write_dirs=allowed_write_dirs,
            enforce_permission_sandbox=enforce_permission_sandbox,
        )

    codex_bin = resolve_executable(codex_bin, cwd=cwd)
    if sandbox_profile == "bypass":
        command = [codex_bin, "exec", "-C", str(cwd), "--dangerously-bypass-approvals-and-sandbox"]
    else:
        command = [codex_bin]
        if search:
            command.append("--search")
        else:
            command.extend(["--disable", "standalone_web_search", "--disable", "browser_use", "--disable", "browser_use_external"])
        sandbox = "read-only" if sandbox_profile == "read-only" else "workspace-write"
        command.extend(["-a", "never", "-s", sandbox, "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules", "-C", str(cwd)])
        if sandbox_profile == "local-write":
            for path in dedupe_paths([cwd / "tmp", *allowed_write_dirs]):
                command.extend(["--add-dir", str(path)])
    command.extend(["--output-last-message", last_message_path, "--json", "-"])
    return command


def build_command_info(task: Mapping[str, Any], *, command: Sequence[str], sandbox_profile: str, respect_task_argv: bool) -> dict[str, Any]:
    raw_argv = [str(part) for part in task.get("argv") or []]
    original_sandbox = infer_argv_sandbox(raw_argv)
    actual_sandbox = infer_argv_sandbox(command)
    permission = task_permission(task)
    info = {
        "respect_task_argv": respect_task_argv,
        "sandbox_profile": sandbox_profile,
        "permission_profile": permission.get("profile"),
        "deny_policy": permission.get("deny_policy"),
        "original_argv_sandbox": original_sandbox,
        "actual_sandbox": actual_sandbox,
        "sandbox_overrode_task_argv": bool(raw_argv and not respect_task_argv and original_sandbox and original_sandbox != actual_sandbox),
        "permission_sandbox_overrode_task_argv": bool(permission.get("profile") and raw_argv and original_sandbox and original_sandbox != actual_sandbox),
    }
    if raw_argv:
        info["original_argv"] = command_to_text(raw_argv)
        info["respect_task_argv_adjusted"] = bool(respect_task_argv and command_to_text(raw_argv) != command_to_text(command))
    if any(str(part) == "--add-dir" for part in command):
        info["additional_writable_dirs"] = [str(command[index + 1]) for index, part in enumerate(command[:-1]) if str(part) == "--add-dir"]
    return info


def task_permission(task: Mapping[str, Any]) -> dict[str, Any]:
    paths = task["_codex_win"] if isinstance(task.get("_codex_win"), Mapping) else {}
    permission = paths.get("permission") if isinstance(paths, Mapping) else None
    return (
        dict(permission)
        if isinstance(permission, Mapping)
        else {"profile": None, "deny_policy": "fail", "allowed_write_dirs": [], "allowed_commands": [], "denied_commands": [], "capabilities": {}, "readonly_equivalents": []}
    )


def effective_task_sandbox(task: Mapping[str, Any], *, default_sandbox: str) -> str:
    permission = task_permission(task)
    permission_sandbox = _optional_str(permission.get("sandbox_profile"))
    if permission_sandbox:
        return permission_sandbox
    task_sandbox = _optional_str(task.get("sandbox_profile"))
    return task_sandbox or default_sandbox


def validate_permission_preflight(task: Mapping[str, Any], *, permission: Mapping[str, Any]) -> dict[str, Any]:
    if not permission.get("profile"):
        return {"failed": False}
    allowed_roots = [Path(str(path)) for path in permission.get("allowed_write_dirs") or []]
    if not allowed_roots:
        return {"failed": False}
    violations: list[dict[str, str]] = []
    paths = task["_codex_win"] if isinstance(task.get("_codex_win"), Mapping) else {}
    for label, value in [
        ("patch_path", paths.get("patch_path") if isinstance(paths, Mapping) else None),
        ("expected_output_path", paths.get("expected_output_path") if isinstance(paths, Mapping) else None),
    ]:
        if value and not path_is_under_any(Path(str(value)), allowed_roots):
            violations.append({"label": label, "path": str(value)})
    for index, output in enumerate(expected_output_contracts(task), start=1):
        output_path = Path(str(output.get("path")))
        if not path_is_under_any(output_path, allowed_roots):
            violations.append({"label": f"expected_outputs[{index}]", "path": str(output_path)})
    if violations:
        return {
            "failed": True,
            "error_type": "permission_output_path_denied",
            "error_summary": f"declared output path is outside allowed_write_dirs: {violations[0]['path']}",
            "violations": violations,
            "allowed_write_dirs": [str(path) for path in allowed_roots],
        }
    return {"failed": False, "allowed_write_dirs": [str(path) for path in allowed_roots]}


def path_is_under_any(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def inject_permission_prelude(prompt_text: str, *, task: Mapping[str, Any], cwd: Path) -> str:
    permission = task_permission(task)
    profile = permission.get("profile")
    if not profile:
        return prompt_text
    expected_outputs = expected_output_contracts(task)
    lines = [
        "Codex-win agent permission boundary:",
        f"- cwd: {cwd}",
        f"- permission_profile: {profile}",
        f"- deny_policy: {permission.get('deny_policy')}",
        f"- deny_policy_meaning: {deny_policy_prompt_hint(str(permission.get('deny_policy') or 'fail'))}",
    ]
    write_dirs = [str(path) for path in permission.get("allowed_write_dirs") or []]
    if write_dirs:
        lines.append("- allowed_write_dirs:")
        lines.extend(f"  - {path}" for path in write_dirs)
    denied = [str(command) for command in permission.get("denied_commands") or []]
    if denied:
        lines.append("- denied_commands:")
        lines.extend(f"  - {command}" for command in denied)
        lines.append("- If a denied command would be useful, do not run it. Continue by producing the declared output.")
    readonly_equivalents = [item for item in permission.get("readonly_equivalents") or [] if isinstance(item, Mapping)]
    if readonly_equivalents:
        lines.append("- readonly_equivalents:")
        for item in readonly_equivalents:
            lines.append(f"  - {item.get('command')} => {item.get('replacement')} ({item.get('status')})")
        lines.append("- Use readonly_equivalents instead of running denied commands.")
        git_context = next((item.get("snapshot") for item in readonly_equivalents if item.get("replacement") in {"git_context_snapshot", "git_status_snapshot"}), None)
        if isinstance(git_context, Mapping):
            lines.extend(render_git_context_snapshot(git_context))
    if expected_outputs:
        lines.append("- expected_outputs:")
        for output in expected_outputs:
            lines.append(f"  - kind={output.get('kind')} path={output.get('path')}")
            if output.get("fallback") == "last_message_marked_block":
                lines.append(f"    fallback markers: {output.get('begin')} ... {output.get('end')}")
        lines.append("- If writing an expected output file fails, emit the exact JSONL payload between the fallback markers.")
    return "\n".join(lines) + "\n\n--- task prompt ---\n" + prompt_text


def render_git_context_snapshot(snapshot: Mapping[str, Any]) -> list[str]:
    lines = [
        "- git_context_snapshot:",
        f"    ok: {bool(snapshot.get('ok'))}",
    ]
    for label in ("repo_root", "branch", "head"):
        if snapshot.get(label):
            lines.append(f"    {label}: {snapshot.get(label)}")
    status = snapshot.get("status") if isinstance(snapshot.get("status"), Mapping) else snapshot
    lines.extend(render_snapshot_lines("status", status))
    diff_stat = snapshot.get("diff_stat") if isinstance(snapshot.get("diff_stat"), Mapping) else None
    if diff_stat:
        lines.extend(render_snapshot_lines("diff_stat", diff_stat))
    changed_files = snapshot.get("changed_files") if isinstance(snapshot.get("changed_files"), Mapping) else None
    if changed_files:
        lines.extend(render_snapshot_lines("changed_files", changed_files))
    return lines


def render_snapshot_lines(label: str, snapshot: Mapping[str, Any]) -> list[str]:
    lines = [
        f"    {label}:",
        f"      ok: {bool(snapshot.get('ok'))}",
        f"      command: {snapshot.get('command')}",
    ]
    if snapshot.get("returncode") is not None:
        lines.append(f"      returncode: {snapshot.get('returncode')}")
    stdout_lines = [str(line) for line in snapshot.get("stdout_lines") or []]
    if stdout_lines:
        lines.append("      stdout_lines:")
        lines.extend(f"        {line}" for line in stdout_lines[:120])
    stderr = str(snapshot.get("stderr") or snapshot.get("error") or "").strip()
    if stderr:
        lines.append(f"      stderr: {stderr[:500]}")
    if snapshot.get("truncated"):
        lines.append("      truncated: true")
    return lines


def deny_policy_prompt_hint(policy: str) -> str:
    normalized = normalize_deny_policy(policy)
    if normalized == "deny-fail":
        return "denied or out-of-profile operations make the task fail"
    if normalized == "deny-continue":
        return "do not run denied operations; continue only by producing the declared output"
    if normalized == "deny-rewrite":
        return "if a denied operation blocks file output, emit the declared JSONL between fallback markers in the final message"
    return normalized


def expected_output_contracts(task: Mapping[str, Any]) -> list[dict[str, Any]]:
    paths = task["_codex_win"] if isinstance(task.get("_codex_win"), Mapping) else {}
    outputs = paths.get("expected_outputs") if isinstance(paths, Mapping) else None
    return [dict(item) for item in outputs] if isinstance(outputs, Sequence) and not isinstance(outputs, (str, bytes)) else []


def dedupe_paths(paths: Sequence[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key and key not in seen:
            seen.add(key)
            result.append(path)
    return result


def normalize_respected_codex_argv(
    argv: list[str],
    *,
    cwd: Path,
    sandbox_profile: str | None = None,
    last_message_path: str,
    allowed_write_dirs: Sequence[Path] = (),
    enforce_permission_sandbox: bool = False,
) -> list[str]:
    exec_index = _codex_exec_index(argv)
    if exec_index is None:
        return argv
    if enforce_permission_sandbox and sandbox_profile:
        enforce_codex_exec_sandbox(argv, exec_index=exec_index, sandbox_profile=sandbox_profile)
    if "--output-last-message" not in argv and last_message_path:
        insert_codex_exec_options(argv, exec_index=exec_index, options=["--output-last-message", last_message_path])
    if "--json" not in argv:
        insert_codex_exec_options(argv, exec_index=exec_index, options=["--json"])
    if infer_argv_sandbox(argv) == "workspace-write":
        for path in dedupe_paths([cwd / "tmp", *allowed_write_dirs]):
            if not argv_has_add_dir(argv, path):
                insert_codex_exec_options(argv, exec_index=exec_index, options=["--add-dir", str(path)])
    if not codex_exec_reads_stdin(argv, exec_index=exec_index) and not codex_exec_has_prompt_argument(argv, exec_index=exec_index):
        argv.append("-")
    return argv


def enforce_codex_exec_sandbox(argv: list[str], *, exec_index: int, sandbox_profile: str) -> None:
    remove_codex_exec_sandbox_options(argv, exec_index=exec_index)
    if sandbox_profile == "bypass":
        insert_codex_exec_options(argv, exec_index=exec_index, options=["--dangerously-bypass-approvals-and-sandbox"])
        return
    sandbox = "read-only" if sandbox_profile == "read-only" else "workspace-write"
    insert_codex_exec_options(argv, exec_index=exec_index, options=["-s", sandbox])


def remove_codex_exec_sandbox_options(argv: list[str], *, exec_index: int) -> None:
    index = exec_index + 1
    prompt_index = codex_exec_prompt_index(argv, exec_index=exec_index)
    while index < (prompt_index if prompt_index is not None else len(argv)):
        part = str(argv[index])
        if part == "--dangerously-bypass-approvals-and-sandbox":
            del argv[index]
            if prompt_index is not None:
                prompt_index -= 1
            continue
        if part in {"-s", "--sandbox"}:
            deleted = min(2, len(argv) - index)
            del argv[index : index + deleted]
            if prompt_index is not None:
                prompt_index -= deleted
            continue
        if part.startswith("--sandbox="):
            del argv[index]
            if prompt_index is not None:
                prompt_index -= 1
            continue
        index += 1


def _codex_exec_index(argv: Sequence[str]) -> int | None:
    for index, part in enumerate(argv):
        if str(part) == "exec":
            return index
    return None


def codex_exec_reads_stdin(argv: Sequence[str], *, exec_index: int | None = None) -> bool:
    if exec_index is None:
        exec_index = _codex_exec_index(argv)
    if exec_index is None:
        return True
    return "-" in [str(part) for part in argv[exec_index + 1 :]]


_CODEX_EXEC_OPTIONS_WITH_VALUE = {
    "-a",
    "--ask-for-approval",
    "-c",
    "--config",
    "--enable",
    "--disable",
    "-i",
    "--image",
    "-m",
    "--model",
    "--local-provider",
    "-p",
    "--profile",
    "-s",
    "--sandbox",
    "-C",
    "--cd",
    "--add-dir",
    "--output-schema",
    "--color",
    "-o",
    "--output-last-message",
}


def codex_exec_has_prompt_argument(argv: Sequence[str], *, exec_index: int) -> bool:
    prompt_index = codex_exec_prompt_index(argv, exec_index=exec_index)
    return prompt_index is not None and str(argv[prompt_index]) != "-"


def codex_exec_prompt_index(argv: Sequence[str], *, exec_index: int) -> int | None:
    skip_next = False
    for index, part in enumerate([str(item) for item in argv[exec_index + 1 :]], start=exec_index + 1):
        if skip_next:
            skip_next = False
            continue
        if part == "-":
            return index
        if part in _CODEX_EXEC_OPTIONS_WITH_VALUE:
            skip_next = True
            continue
        if part.startswith("--") and "=" in part:
            continue
        if part.startswith("-"):
            continue
        return index
    return None


def insert_codex_exec_options(argv: list[str], *, exec_index: int, options: Sequence[str]) -> None:
    prompt_index = codex_exec_prompt_index(argv, exec_index=exec_index)
    insert_at = prompt_index if prompt_index is not None else len(argv)
    argv[insert_at:insert_at] = [str(option) for option in options]


def argv_has_add_dir(argv: Sequence[str], path: Path) -> bool:
    expected = str(path)
    for index, part in enumerate(argv[:-1]):
        if str(part) == "--add-dir" and str(argv[index + 1]) == expected:
            return True
    return False


def infer_argv_sandbox(argv: Sequence[str]) -> str | None:
    parts = [str(part) for part in argv]
    if any(part == "--dangerously-bypass-approvals-and-sandbox" for part in parts):
        return "bypass"
    for index, part in enumerate(parts):
        if part in {"-s", "--sandbox"} and index + 1 < len(parts):
            return parts[index + 1]
        if part.startswith("--sandbox="):
            return part.split("=", 1)[1]
    return None


def status_run(output_root: str | Path) -> dict[str, Any]:
    _reap_background_procs()
    status = read_status(output_root)
    if status.get("status") in {"starting", "running"}:
        pid = status.get("supervisor_pid")
        if isinstance(pid, int) and not is_process_running(pid):
            status = dict(status)
            status["supervisor_alive"] = False
        elif isinstance(pid, int):
            status = dict(status)
            status["supervisor_alive"] = True
    return status


def wait_run(output_root: str | Path, *, timeout_seconds: int = 0, poll_seconds: float = 1.0) -> dict[str, Any]:
    started = time.perf_counter()
    while True:
        status = status_run(output_root)
        if str(status.get("status")) in TERMINAL_RUN_STATUSES:
            _reap_background_procs()
            return status
        if timeout_seconds > 0 and time.perf_counter() - started >= timeout_seconds:
            status = dict(status)
            status["wait_timeout"] = True
            return status
        time.sleep(max(0.1, poll_seconds))


def kill_run(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root)
    status = read_status(root)
    target_pids = active_pids(root, status)
    killed = kill_pids(target_pids)
    updated = dict(status)
    updated["status"] = "killed"
    updated["target_pids"] = target_pids
    updated["killed_pids"] = killed
    updated["finished_at"] = utc_now_iso()
    updated["updated_at"] = updated["finished_at"]
    _mark_unfinished_tasks(updated, task_status="killed", at=updated["finished_at"])
    _write_json_atomic(root / STATUS_FILE, updated)
    append_jsonl_safe(root / CHILDREN_FILE, {"event": "run_killed", "pids": killed, "target_pids": target_pids, "at": updated["finished_at"]})
    _reap_background_procs(wait=True)
    wait_for_run_files_released(root, timeout_seconds=6.0)
    return updated


def cleanup_stale_run(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root)
    status = status_run(root)
    if status.get("status") not in {"starting", "running"}:
        return status
    if status.get("supervisor_alive") is True:
        return status
    target_pids = active_pids(root, status)
    killed = kill_pids(target_pids)
    updated = dict(status)
    updated["status"] = "stale_cleaned"
    updated["target_pids"] = target_pids
    updated["killed_pids"] = killed
    updated["finished_at"] = utc_now_iso()
    updated["updated_at"] = updated["finished_at"]
    _mark_unfinished_tasks(updated, task_status="stale_cleaned", at=updated["finished_at"])
    _write_json_atomic(root / STATUS_FILE, updated)
    append_jsonl_safe(root / CHILDREN_FILE, {"event": "stale_cleaned", "pids": killed, "target_pids": target_pids, "at": updated["finished_at"]})
    _reap_background_procs(wait=True)
    wait_for_run_files_released(root, timeout_seconds=6.0)
    return updated


def collect_run(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root)
    tasks_path = root / TASKS_FILE
    results_path = root / RESULTS_FILE
    tasks = load_jsonl(tasks_path) if tasks_path.exists() else []
    results = load_jsonl(results_path) if results_path.exists() else []
    issues: list[dict[str, Any]] = []
    task_codes = [str(task.get("task_code") or "") for task in tasks]
    duplicates = [code for code, count in Counter(task_codes).items() if code and count > 1]
    for code in duplicates:
        issues.append({"severity": "error", "code": "duplicate_task_code", "task_code": code})

    result_codes = [str(row.get("task_code") or "") for row in results]
    duplicate_results = [code for code, count in Counter(result_codes).items() if code and count > 1]
    for code in duplicate_results:
        issues.append({"severity": "warning", "code": "duplicate_result", "task_code": code})

    planned_codes = planned_task_codes(root)
    for task in tasks:
        task_code = str(task.get("task_code") or "")
        if task_code in planned_codes:
            continue
        paths = task.get("_codex_win") if isinstance(task.get("_codex_win"), Mapping) else {}
        _check_text_output(paths.get("last_message_path"), task_code=task_code, label="last_message", issues=issues)
        _check_jsonl_output(paths.get("patch_path"), task_code=task_code, label="patch", issues=issues, allow_missing=True)
        _check_jsonl_output(paths.get("event_log_path"), task_code=task_code, label="event_log", issues=issues, allow_missing=True)

    summary = {
        "schema": "codex-win.agent.collect.v1",
        "output_root": str(root.resolve()),
        "status": status_run(root).get("status") if (root / STATUS_FILE).exists() else "unknown",
        "totals": {
            "tasks": len(tasks),
            "results": len(results),
            **dict(Counter(str(row.get("status") or "unknown") for row in results)),
        },
        "issues": issues,
        "ok": not any(issue.get("severity") == "error" for issue in issues),
    }
    write_json_utf8(root / SUMMARY_FILE, summary, sort_keys=True)
    return summary


def active_pids(output_root: Path, status: Mapping[str, Any]) -> list[int]:
    pids: list[int] = []
    supervisor_pid = status.get("supervisor_pid")
    for task in status.get("tasks") or []:
        if not isinstance(task, Mapping):
            continue
        pid = task.get("pid")
        if isinstance(pid, int):
            pids.append(pid)
    children_path = output_root / CHILDREN_FILE
    if children_path.exists():
        for row in load_jsonl(children_path):
            if row.get("event") in {"supervisor_started", "supervisor_active"}:
                continue
            pid = row.get("pid")
            if isinstance(pid, int) and pid != supervisor_pid:
                pids.append(pid)
    if isinstance(supervisor_pid, int):
        pids.append(supervisor_pid)
    ordered: list[int] = []
    seen: set[int] = set()
    for pid in pids:
        if pid > 0 and pid not in seen:
            ordered.append(pid)
            seen.add(pid)
    return ordered


def planned_task_codes(output_root: Path) -> set[str]:
    codes: set[str] = set()
    status_path = output_root / STATUS_FILE
    if status_path.exists():
        status = read_status(output_root)
        if status.get("status") == "planned":
            for task in status.get("tasks") or []:
                if isinstance(task, Mapping) and str(task.get("task_code") or ""):
                    codes.add(str(task.get("task_code")))
    results_path = output_root / RESULTS_FILE
    if results_path.exists():
        for row in load_jsonl(results_path):
            if row.get("status") == "planned" and str(row.get("task_code") or ""):
                codes.add(str(row.get("task_code")))
    return codes


def resolve_executable(executable: str, *, cwd: Path) -> str:
    raw = str(executable).strip()
    if not raw:
        raise AgentRunError("missing executable")
    path = Path(raw)
    if path.is_absolute():
        if path.exists():
            return str(path.resolve())
        raise AgentRunError(f"executable path does not exist: {path}")
    cwd_candidate = (cwd / path).resolve()
    if cwd_candidate.exists():
        return str(cwd_candidate)
    has_path_separator = "\\" in raw or "/" in raw or path.parent != Path(".")
    if has_path_separator:
        raise AgentRunError(f"relative executable path does not exist from cwd {cwd}: {raw}")
    resolved = shutil.which(raw)
    return resolved or raw


def _reap_background_procs(*, wait: bool = False) -> None:
    active: list[subprocess.Popen[str]] = []
    for proc in _BACKGROUND_PROCS:
        if wait:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                active.append(proc)
            continue
        if proc.poll() is None:
            active.append(proc)
        else:
            try:
                proc.wait(timeout=0)
            except subprocess.TimeoutExpired:
                active.append(proc)
    _BACKGROUND_PROCS[:] = active


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _is_windows_process_running(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_windows_process_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def kill_pids(pids: Sequence[int]) -> list[int]:
    killed: list[int] = []
    for pid in pids:
        if kill_process_tree(pid):
            killed.append(pid)
    wait_until_stopped(pids, timeout_seconds=2.0)
    return killed


def wait_until_stopped(pids: Sequence[int], *, timeout_seconds: float) -> None:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        if not any(is_process_running(pid) for pid in pids):
            return
        time.sleep(0.05)


def wait_for_run_files_released(output_root: Path, *, timeout_seconds: float) -> None:
    if os.name != "nt":
        return
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        paths = list(output_root.glob("*.log"))
        logs_dir = output_root / "logs"
        if logs_dir.exists():
            paths.extend(path for path in logs_dir.iterdir() if path.is_file())
        if all(_windows_file_can_open_exclusive(path) for path in paths):
            return
        time.sleep(0.05)


def _windows_file_can_open_exclusive(path: Path) -> bool:
    if not path.exists():
        return True
    import ctypes
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(str(path), GENERIC_READ, 0, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if handle == INVALID_HANDLE_VALUE:
        return False
    kernel32.CloseHandle(handle)
    return True


def kill_process_tree(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        tree_pids = _windows_process_tree_pids(pid)
        was_running = any(is_process_running(target_pid) for target_pid in tree_pids)
        returncodes: list[int] = []
        for target_pid in tree_pids:
            completed = subprocess.run(["taskkill", "/PID", str(target_pid), "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            returncodes.append(completed.returncode)
        wait_until_stopped(tree_pids, timeout_seconds=2.0)
        return was_running or any(returncode == 0 for returncode in returncodes)
    was_running = is_process_running(pid)
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.2)
    if is_process_running(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return was_running


def _windows_process_tree_pids(pid: int) -> list[int]:
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    MAX_PATH = 260
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * MAX_PATH),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return [pid]
    parents: dict[int, list[int]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return [pid]
        while True:
            process_id = int(entry.th32ProcessID)
            parent_id = int(entry.th32ParentProcessID)
            parents.setdefault(parent_id, []).append(process_id)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)

    ordered: list[int] = []
    seen: set[int] = set()

    def visit(current_pid: int) -> None:
        if current_pid in seen:
            return
        seen.add(current_pid)
        for child_pid in parents.get(current_pid, []):
            visit(child_pid)
        ordered.append(current_pid)

    visit(pid)
    return ordered or [pid]


def start_stdin_writer(proc: subprocess.Popen[str], prompt_text: str, stdin_status: dict[str, Any]) -> threading.Thread:
    def write_prompt() -> None:
        if proc.stdin is None:
            stdin_status["error"] = "stdin pipe missing"
            return
        try:
            proc.stdin.write(prompt_text)
            proc.stdin.flush()
            stdin_status["written"] = True
            stdin_status["bytes"] = len(prompt_text.encode("utf-8"))
        except (BrokenPipeError, OSError, ValueError) as exc:
            stdin_status["error"] = str(exc)
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    thread = threading.Thread(target=write_prompt, name=f"codex-win-stdin-{proc.pid}", daemon=True)
    thread.start()
    return thread


def _mark_unfinished_tasks(payload: dict[str, Any], *, task_status: str, at: str) -> None:
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if str(task.get("status") or "") in TERMINAL_TASK_STATUSES:
            continue
        task["status"] = task_status
        task["finished_at"] = at
        task["last_seen_at"] = at
    payload["totals"] = dict(Counter(str(task.get("status") or "unknown") for task in tasks if isinstance(task, Mapping)))


def analyze_codex_events(path: Path) -> dict[str, Any]:
    analysis: dict[str, Any] = {"failed": False, "usage": {}, "failure_events": []}
    if not path.exists():
        return analysis
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            analysis["usage"] = dict(event["usage"])
        message = event_message(event)
        lower = message.lower()
        failed = event_type in EVENT_FAILURE_TYPES or any(word in lower for word in USAGE_FAILURE_WORDS)
        if failed:
            error_type = classify_failure_message(message, default="codex_event_failed")
            failure = {"line": line_number, "type": event_type, "error_type": error_type, "message": message[:500]}
            analysis["failure_events"].append(failure)
            analysis["failed"] = True
            analysis.setdefault("error_type", error_type)
            analysis.setdefault("error_summary", failure["message"] or f"event type={event_type}")
    if analysis.get("failed") and not analysis.get("error_summary"):
        analysis["error_summary"] = "Codex event log reported failure"
    return analysis


def event_message(event: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("message", "error", "reason"):
        value = event.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Mapping):
            nested = event_message(value)
            if nested:
                parts.append(nested)
    return " ".join(part for part in parts if part)


def analyze_process_text(stderr_path: Path, last_message_path: Path) -> dict[str, Any]:
    text_parts: list[str] = []
    for path in (stderr_path, last_message_path):
        if path.exists():
            text_parts.append(path.read_text(encoding="utf-8", errors="replace")[:2000])
    text = "\n".join(text_parts)
    lower = text.lower()
    if any(word in lower for word in USAGE_FAILURE_WORDS):
        return {"failed": True, "error_type": classify_failure_message(text, default="process_output_failed"), "error_summary": text[:500]}
    if "policy" in lower and any(word in lower for word in POLICY_FAILURE_WORDS):
        return {"failed": True, "error_type": "policy_denied", "error_summary": text[:500]}
    return {"failed": False}


def resolve_deny_policy(
    *,
    permission: Mapping[str, Any],
    process_analysis: Mapping[str, Any],
    permission_analysis: Mapping[str, Any],
    output_analysis: Mapping[str, Any],
) -> dict[str, Any]:
    policy = normalize_deny_policy(permission.get("deny_policy") or "fail")
    process = dict(process_analysis)
    perm = dict(permission_analysis)
    output_ok = not bool(output_analysis.get("failed"))
    process_policy_failed = bool(process.get("failed")) and process.get("error_type") == "policy_denied"
    permission_failed = bool(perm.get("failed"))
    resolution: dict[str, Any] = {
        "policy": policy,
        "action": "none",
        "output_contract_satisfied": output_ok,
    }
    if not process_policy_failed and not permission_failed:
        return {"process_analysis": process, "permission_analysis": perm, "resolution": resolution}
    if policy == "deny-fail":
        resolution["action"] = "fail"
        resolution["reason"] = "permission or process policy evidence was observed"
        return {"process_analysis": process, "permission_analysis": perm, "resolution": resolution}
    if policy == "deny-continue":
        resolution["action"] = "continue" if output_ok else "fail"
        resolution["reason"] = "continue only when output contract is satisfied"
    elif policy == "deny-rewrite":
        recovery_ok = output_has_recovery(output_analysis)
        resolution["action"] = "rewrite_to_last_message" if output_ok and recovery_ok else "fail"
        resolution["reason"] = "rewrite requires a successful last-message recovery"
        resolution["recovered_from_last_message"] = recovery_ok
    if resolution.get("action") in {"continue", "rewrite_to_last_message"}:
        if process_policy_failed:
            process["failed"] = False
            process["downgraded_by_deny_policy"] = True
        if permission_failed:
            perm["failed"] = False
            perm["downgraded_by_deny_policy"] = True
    return {"process_analysis": process, "permission_analysis": perm, "resolution": resolution}


def output_has_recovery(output_analysis: Mapping[str, Any]) -> bool:
    return any(bool(recovery.get("ok")) for recovery in output_analysis.get("recoveries") or [] if isinstance(recovery, Mapping))


def analyze_permission_output(task: Mapping[str, Any], *paths: Path) -> dict[str, Any]:
    permission = task_permission(task)
    denied = [str(command).strip() for command in permission.get("denied_commands") or [] if str(command).strip()]
    capabilities = permission.get("capabilities") if isinstance(permission.get("capabilities"), Mapping) else {}
    text_parts: list[str] = []
    for path in paths:
        if path.exists():
            text_parts.append(path.read_text(encoding="utf-8", errors="replace")[:4000])
    text = "\n".join(text_parts)
    lower = text.lower()
    mentions = [command for command in denied if command_mentioned(command, lower)]
    events: list[dict[str, Any]] = []
    for command in mentions:
        events.append(
            {
                "type": "denied_command_mentioned",
                "command": command,
                "severity": "error",
                "error_type": "permission_denied_command",
                "message": f"output mentioned denied command: {command}",
            }
        )
    if bool(capabilities) and not bool(capabilities.get("git_write")):
        for command in regex_matches(GIT_WRITE_RE, text):
            events.append(
                {
                    "type": "git_write_command_mentioned",
                    "command": command,
                    "severity": "error",
                    "error_type": "permission_git_write_denied",
                    "message": f"git write command is not allowed by profile: {command}",
                }
            )
    if bool(capabilities) and not bool(capabilities.get("git_read")):
        for command in regex_matches(GIT_READ_RE, text):
            events.append(
                {
                    "type": "git_read_command_mentioned",
                    "command": command,
                    "severity": "error",
                    "error_type": "permission_git_read_denied",
                    "message": f"git read command is not allowed by profile: {command}",
                }
            )
    if bool(capabilities) and not bool(capabilities.get("database")):
        for command in regex_matches(DB_COMMAND_RE, text):
            events.append(
                {
                    "type": "database_command_mentioned",
                    "command": command,
                    "severity": "error",
                    "error_type": "permission_database_denied",
                    "message": f"database command is not allowed by profile: {command}",
                }
            )
    if bool(capabilities) and not bool(capabilities.get("network")):
        for command in regex_matches(NETWORK_COMMAND_RE, text):
            events.append(
                {
                    "type": "network_command_mentioned",
                    "command": command,
                    "severity": "error",
                    "error_type": "permission_network_denied",
                    "message": f"network command is not allowed by profile: {command}",
                }
            )
    if "policy" in lower and any(word in lower for word in POLICY_FAILURE_WORDS):
        events.append(
            {
                "type": "policy_denied_output",
                "severity": "warning",
                "error_type": "policy_denied",
                "message": first_nonempty_line(text)[:500],
            }
        )
    failure_events = [event for event in events if event.get("severity") == "error"]
    analysis: dict[str, Any] = {
        "failed": bool(failure_events),
        "deny_policy": permission.get("deny_policy"),
        "profile": permission.get("profile"),
        "allowed_commands": list(permission.get("allowed_commands") or []),
        "denied_commands": denied,
        "denied_command_mentions": mentions,
        "capabilities": dict(capabilities),
        "events": dedupe_permission_events(events),
    }
    if failure_events:
        first = failure_events[0]
        analysis["error_type"] = first.get("error_type") or "permission_policy_failed"
        analysis["error_summary"] = first.get("message") or "permission policy reported failure"
    return analysis


def command_mentioned(command: str, lower_text: str) -> bool:
    needle = command.lower().strip()
    if not needle:
        return False
    if re.fullmatch(r"[\w.-]+", needle):
        return re.search(rf"(?<![\w.-]){re.escape(needle)}(?![\w.-])", lower_text) is not None
    return needle in lower_text


def regex_matches(pattern: re.Pattern[str], text: str) -> list[str]:
    seen: set[str] = set()
    matches: list[str] = []
    for match in pattern.finditer(text):
        command = " ".join(match.group(0).split())
        key = command.lower()
        if key in seen:
            continue
        seen.add(key)
        matches.append(command)
    return matches


def dedupe_permission_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for event in events:
        key = (str(event.get("type") or ""), str(event.get("command") or event.get("message") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(event))
    return result


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def classify_failure_message(message: str, *, default: str) -> str:
    lower = message.lower()
    if "usage limit" in lower or "quota" in lower or "credit" in lower:
        return "usage_limit"
    if "rate limit" in lower:
        return "rate_limit"
    if "auth" in lower or "unauthorized" in lower or "forbidden" in lower:
        return "auth_error"
    if "policy" in lower and any(word in lower for word in POLICY_FAILURE_WORDS):
        return "policy_denied"
    return default


def check_task_outputs(task: Mapping[str, Any], paths: Mapping[str, Any], *, last_message_path: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    patch_path = paths.get("patch_path")
    if patch_path:
        patch_file = Path(str(patch_path))
        if not patch_file.exists() and bool(task.get("patch_fallback_from_last_message")):
            recoveries.append(recover_patch_from_last_message(last_message_path, patch_file))
        checks.append(check_expected_file(patch_file, label="patch", required=True, min_bytes=1, min_lines=None))
    expected_path = paths.get("expected_output_path")
    if expected_path:
        checks.append(
            check_expected_file(
                Path(str(expected_path)),
                label="expected_output",
                required=True,
                min_bytes=_optional_int(task.get("expected_min_bytes")),
                min_lines=_optional_int(task.get("expected_line_count")),
            )
        )
    for index, output in enumerate(expected_output_contracts(task), start=1):
        output_path = Path(str(output["path"]))
        if not output_path.exists() and output.get("fallback") == "last_message_marked_block":
            recoveries.append(recover_marked_jsonl_from_last_message(last_message_path, output_path, begin=str(output.get("begin") or ""), end=str(output.get("end") or "")))
        check = check_expected_file(
            output_path,
            label=f"expected_output:{output.get('kind')}:{index}",
            required=True,
            min_bytes=_optional_int(output.get("expected_min_bytes")) or 1,
            min_lines=_optional_int(output.get("expected_line_count")),
        )
        if check.get("ok") and output.get("kind") == "jsonl_patch":
            check.update(check_jsonl_file(output_path))
        checks.append(check)
    failed_checks = [check for check in checks if not check.get("ok")]
    if failed_checks:
        first = failed_checks[0]
        return {
            "failed": True,
            "error_type": str(first.get("code") or "missing_expected_output"),
            "error_summary": str(first.get("message") or "expected output contract was not satisfied"),
            "checks": checks,
            "recoveries": recoveries,
        }
    return {"failed": False, "checks": checks, "recoveries": recoveries}


def recover_patch_from_last_message(last_message_path: Path, patch_path: Path) -> dict[str, Any]:
    if not last_message_path.exists():
        return {"ok": False, "source": str(last_message_path), "path": str(patch_path), "code": "last_message_missing"}
    text = last_message_path.read_text(encoding="utf-8", errors="replace")
    candidates = extract_structured_jsonl_candidates(text)
    for candidate in candidates:
        rows = parse_jsonl_candidate(candidate)
        if not rows:
            continue
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        with patch_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
        return {"ok": True, "source": str(last_message_path), "path": str(patch_path), "rows": len(rows), "bytes": patch_path.stat().st_size}
    return {"ok": False, "source": str(last_message_path), "path": str(patch_path), "code": "no_structured_jsonl"}


def recover_marked_jsonl_from_last_message(last_message_path: Path, output_path: Path, *, begin: str, end: str) -> dict[str, Any]:
    if not begin or not end:
        return {"ok": False, "source": str(last_message_path), "path": str(output_path), "code": "missing_markers"}
    if not last_message_path.exists():
        return {"ok": False, "source": str(last_message_path), "path": str(output_path), "code": "last_message_missing"}
    text = last_message_path.read_text(encoding="utf-8", errors="replace")
    start = text.find(begin)
    if start < 0:
        return {"ok": False, "source": str(last_message_path), "path": str(output_path), "code": "begin_marker_missing"}
    start += len(begin)
    finish = text.find(end, start)
    if finish < 0:
        return {"ok": False, "source": str(last_message_path), "path": str(output_path), "code": "end_marker_missing"}
    rows = parse_jsonl_candidate(text[start:finish].strip())
    if not rows:
        return {"ok": False, "source": str(last_message_path), "path": str(output_path), "code": "invalid_marked_jsonl"}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
    return {"ok": True, "source": str(last_message_path), "path": str(output_path), "rows": len(rows), "bytes": output_path.stat().st_size}


def extract_structured_jsonl_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    marker = "```"
    position = 0
    while True:
        start = text.find(marker, position)
        if start < 0:
            break
        line_end = text.find("\n", start + len(marker))
        if line_end < 0:
            break
        info = text[start + len(marker) : line_end].strip().lower()
        end = text.find(marker, line_end + 1)
        if end < 0:
            break
        body = text[line_end + 1 : end].strip()
        if info in {"json", "jsonl", "ndjson"} and body:
            candidates.append(body)
        position = end + len(marker)
    stripped = text.strip()
    if stripped.startswith("["):
        candidates.append(stripped)
    elif "\n" in stripped and all(line.lstrip().startswith("{") for line in stripped.splitlines() if line.strip()):
        candidates.append(stripped)
    return candidates


def parse_jsonl_candidate(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line in stripped.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                return []
            if not isinstance(row, dict):
                return []
            rows.append(row)
        return rows
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return [dict(row) for row in value]
    if isinstance(value, dict):
        return [dict(value)]
    return []


def check_expected_file(path: Path, *, label: str, required: bool, min_bytes: int | None, min_lines: int | None) -> dict[str, Any]:
    if not path.exists():
        return {"ok": not required, "label": label, "path": str(path), "code": "missing_expected_output", "message": f"{label} missing: {path}"}
    size = path.stat().st_size
    if min_bytes is not None and size < min_bytes:
        return {
            "ok": False,
            "label": label,
            "path": str(path),
            "code": "expected_output_too_small",
            "message": f"{label} has {size} bytes, expected at least {min_bytes}",
            "bytes": size,
            "expected_min_bytes": min_bytes,
        }
    line_count = None
    if min_lines is not None:
        line_count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if line_count < min_lines:
            return {
                "ok": False,
                "label": label,
                "path": str(path),
                "code": "expected_output_too_few_lines",
                "message": f"{label} has {line_count} lines, expected at least {min_lines}",
                "line_count": line_count,
                "expected_line_count": min_lines,
            }
    result = {"ok": True, "label": label, "path": str(path), "bytes": size}
    if line_count is not None:
        result["line_count"] = line_count
    return result


def check_jsonl_file(path: Path) -> dict[str, Any]:
    line_count = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        line_count += 1
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            return {"ok": False, "code": "invalid_jsonl", "message": f"invalid JSONL at {path}:{line_number}: {exc}"}
        if not isinstance(value, dict):
            return {"ok": False, "code": "invalid_jsonl", "message": f"JSONL row must be an object at {path}:{line_number}"}
    if line_count == 0:
        return {"ok": False, "code": "empty_jsonl", "message": f"JSONL file is empty: {path}"}
    return {"ok": True, "line_count": line_count}


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def usage_from_events(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    usage: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = dict(event["usage"])
    return usage


class AgentRunState:
    def __init__(
        self,
        *,
        output_root: Path,
        tasks: Sequence[Mapping[str, Any]],
        max_workers: int,
        timeout_seconds: int,
        sandbox_profile: str,
        background: bool,
    ) -> None:
        self.output_root = output_root
        self.run_id = uuid.uuid4().hex
        self.started_at = utc_now_iso()
        self.max_workers = max_workers
        self.timeout_seconds = timeout_seconds
        self.sandbox_profile = sandbox_profile
        self.background = background
        self.lock = threading.Lock()
        self.task_records: dict[str, dict[str, Any]] = {
            str(task["task_code"]): {"task_code": str(task["task_code"]), "status": "pending"} for task in tasks
        }

    def update_task(self, task_code: str, **fields: Any) -> None:
        with self.lock:
            record = self.task_records.setdefault(task_code, {"task_code": task_code})
            record.update(fields)
            self._write_locked(status="running")

    def append_result(self, result: Mapping[str, Any]) -> None:
        with self.lock:
            append_jsonl_safe(self.output_root / RESULTS_FILE, dict(result))

    def write(self, *, status: str) -> None:
        with self.lock:
            self._write_locked(status=status)

    def _write_locked(self, *, status: str) -> None:
        now = utc_now_iso()
        records = list(self.task_records.values())
        payload = {
            "schema": "codex-win.agent.status.v1",
            "run_id": self.run_id,
            "status": status,
            "started_at": self.started_at,
            "updated_at": now,
            "output_root": str(self.output_root),
            "supervisor_pid": os.getpid(),
            "background": self.background,
            "max_workers": self.max_workers,
            "timeout_seconds": self.timeout_seconds,
            "sandbox_profile": self.sandbox_profile,
            "totals": dict(Counter(str(row.get("status") or "unknown") for row in records)),
            "tasks_total": len(records),
            "tasks": records,
            "files": _run_files(self.output_root),
        }
        _write_json_atomic(self.output_root / STATUS_FILE, payload)


def _finish_run(state: AgentRunState, final_status: str) -> dict[str, Any]:
    with state.lock:
        status_path = state.output_root / STATUS_FILE
        payload = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        payload["status"] = final_status
        payload["finished_at"] = utc_now_iso()
        payload["updated_at"] = payload["finished_at"]
        records = list(state.task_records.values())
        payload["totals"] = dict(Counter(str(row.get("status") or "unknown") for row in records))
        payload["tasks"] = records
        _write_json_atomic(status_path, payload)
        write_json_utf8(state.output_root / SUMMARY_FILE, payload, sort_keys=True)
        return payload


def _check_text_output(value: Any, *, task_code: str, label: str, issues: list[dict[str, Any]]) -> None:
    if not value:
        return
    path = Path(str(value))
    if not path.exists():
        issues.append({"severity": "warning", "code": "missing_output", "task_code": task_code, "label": label, "path": str(path)})
        return
    if not path.read_text(encoding="utf-8", errors="replace").strip():
        issues.append({"severity": "warning", "code": "empty_output", "task_code": task_code, "label": label, "path": str(path)})


def _check_jsonl_output(value: Any, *, task_code: str, label: str, issues: list[dict[str, Any]], allow_missing: bool) -> None:
    if not value:
        return
    path = Path(str(value))
    if not path.exists():
        severity = "warning" if allow_missing else "error"
        issues.append({"severity": severity, "code": "missing_output", "task_code": task_code, "label": label, "path": str(path)})
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        issues.append({"severity": "warning", "code": "empty_output", "task_code": task_code, "label": label, "path": str(path)})
        return
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(
                {
                    "severity": "error",
                    "code": "invalid_jsonl",
                    "task_code": task_code,
                    "label": label,
                    "path": str(path),
                    "line": line_number,
                    "message": str(exc),
                }
            )
            continue
        if not isinstance(value, dict):
            issues.append({"severity": "error", "code": "jsonl_non_object", "task_code": task_code, "label": label, "path": str(path), "line": line_number})


def _run_files(output_root: Path) -> dict[str, str]:
    return {
        "status": str(output_root / STATUS_FILE),
        "tasks": str(output_root / TASKS_FILE),
        "children": str(output_root / CHILDREN_FILE),
        "results": str(output_root / RESULTS_FILE),
        "summary": str(output_root / SUMMARY_FILE),
    }


def _resolve_existing_or_declared_path(value: Any, cwd: Path) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    last_error: OSError | None = None
    for attempt in range(10):
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp_path, path)
            return
        except OSError as exc:
            last_error = exc
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            time.sleep(0.02 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _reset_run_logs(output_root: Path, *, keep_children: bool) -> None:
    for name in (RESULTS_FILE, SUMMARY_FILE):
        path = output_root / name
        if path.exists():
            path.unlink()
    children_path = output_root / CHILDREN_FILE
    if children_path.exists() and not keep_children:
        children_path.unlink()
