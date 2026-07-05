from __future__ import annotations

import json
import os
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
from .timing import append_jsonl, command_to_text, duration_sec, utc_now_iso


STATUS_FILE = "status.json"
TASKS_FILE = "tasks.jsonl"
CHILDREN_FILE = "children.jsonl"
RESULTS_FILE = "results.jsonl"
SUMMARY_FILE = "summary.json"
CONFIG_FILE = "run_config.json"
TERMINAL_RUN_STATUSES = {"planned", "succeeded", "failed", "killed", "stale_cleaned"}
TERMINAL_TASK_STATUSES = {"planned", "succeeded", "failed", "timed_out", "skipped", "killed", "stale_cleaned"}
SANDBOX_PROFILES = {"read-only", "local-write", "bypass"}
_BACKGROUND_PROCS: list[subprocess.Popen[str]] = []


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
    flags: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        flags["creationflags"] = creationflags
    else:
        flags["start_new_session"] = True

    env = build_runtime_env()
    src_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with stdout_path.open("a", encoding="utf-8", newline="\n") as stdout, stderr_path.open("a", encoding="utf-8", newline="\n") as stderr:
        proc = subprocess.Popen(command, cwd=str(cwd), stdout=stdout, stderr=stderr, env=env, **flags)
    _BACKGROUND_PROCS.append(proc)

    append_jsonl(output_root / CHILDREN_FILE, {"event": "supervisor_started", "pid": proc.pid, "at": utc_now_iso(), "command": command_to_text(command)})
    initial_status["supervisor_pid"] = proc.pid
    initial_status["command"] = command_to_text(command)
    return initial_status


def run_plan_foreground(config: Mapping[str, Any], *, launched_in_background: bool) -> dict[str, Any]:
    tasks_path = Path(str(config["tasks_jsonl"])).resolve()
    output_root = Path(str(config["output_root"])).resolve()
    cwd = Path(str(config.get("cwd") or os.getcwd())).resolve()
    max_workers = max(1, int(config.get("max_workers") or 1))
    timeout_seconds = max(1, int(config.get("timeout_seconds") or 1))
    sandbox_profile = str(config.get("sandbox_profile") or "read-only")
    if sandbox_profile not in SANDBOX_PROFILES:
        raise AgentRunError(f"unsupported sandbox profile: {sandbox_profile}")
    heartbeat_seconds = max(0.2, float(config.get("heartbeat_seconds") or 1.0))
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "logs").mkdir(parents=True, exist_ok=True)
    _reset_run_logs(output_root, keep_children=launched_in_background)

    raw_tasks = load_jsonl(tasks_path)
    tasks = normalize_tasks(raw_tasks, cwd=cwd, output_root=output_root)
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
    append_jsonl(output_root / CHILDREN_FILE, {"event": "supervisor_active", "pid": os.getpid(), "at": utc_now_iso()})

    if bool(config.get("dry_run")):
        for task in tasks:
            state.update_task(str(task["task_code"]), status="planned", updated_at=utc_now_iso())
            append_jsonl(output_root / RESULTS_FILE, {"task_code": task["task_code"], "status": "planned"})
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


def normalize_tasks(tasks: Sequence[Mapping[str, Any]], *, cwd: Path, output_root: Path) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
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
        row["task_code"] = task_code
        row["_codex_win"] = {
            "task_index": index,
            "prompt_path": str(prompt_path),
            "patch_path": str(patch_path) if patch_path is not None else None,
            "last_message_path": str(last_message_path),
            "event_log_path": str(event_log_path),
            "stdout_path": str(event_log_path),
            "stderr_path": str(output_root / "logs" / f"{task_code}.stderr.log"),
        }
        normalized.append(row)
    return normalized


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
    prompt_text = prompt_path.read_text(encoding="utf-8")

    timeout = int(task.get("timeout_seconds") or timeout_seconds)
    command = build_task_command(
        task,
        cwd=cwd,
        codex_bin=codex_bin,
        sandbox_profile=sandbox_profile,
        respect_task_argv=respect_task_argv,
        search=search,
    )
    started_at = utc_now_iso()
    started = time.perf_counter()
    stdout_file = stdout_path.open("w", encoding="utf-8", newline="\n")
    stderr_file = stderr_path.open("w", encoding="utf-8", newline="\n")
    proc: subprocess.Popen[str] | None = None
    stdin_writer: threading.Thread | None = None
    try:
        flags: dict[str, Any] = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            flags["start_new_session"] = True
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
            **flags,
        )
        state.update_task(task_code, status="running", pid=proc.pid, started_at=started_at, last_seen_at=started_at, command=command_to_text(command))
        append_jsonl(
            output_root / CHILDREN_FILE,
            {"event": "task_started", "task_code": task_code, "pid": proc.pid, "at": started_at, "command": command_to_text(command)},
        )
        stdin_writer = start_stdin_writer(proc, prompt_text)
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
    status = "timed_out" if timed_out else ("succeeded" if int(returncode) == 0 else "failed")
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
        "usage": usage_from_events(stdout_path),
    }
    state.append_result(result)
    state.update_task(
        task_code,
        status=status,
        returncode=int(returncode),
        timed_out=timed_out,
        finished_at=finished_at,
        last_seen_at=finished_at,
        duration_sec=elapsed,
    )
    append_jsonl(output_root / CHILDREN_FILE, {"event": "task_finished", "task_code": task_code, "pid": result["pid"], "at": finished_at, "status": status})
    return result


def build_task_command(
    task: Mapping[str, Any],
    *,
    cwd: Path,
    codex_bin: str,
    sandbox_profile: str,
    respect_task_argv: bool,
    search: bool,
) -> list[str]:
    if respect_task_argv:
        argv = [str(part) for part in task.get("argv") or []]
        if not argv:
            raise AgentRunError(f"task {task.get('task_code')} has no argv")
        argv[0] = resolve_executable(argv[0], cwd=cwd)
        return argv

    paths = task["_codex_win"] if isinstance(task.get("_codex_win"), Mapping) else {}
    last_message_path = str(paths.get("last_message_path"))
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
    command.extend(["--output-last-message", last_message_path, "--json", "-"])
    return command


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
    append_jsonl(root / CHILDREN_FILE, {"event": "run_killed", "pids": killed, "target_pids": target_pids, "at": updated["finished_at"]})
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
    append_jsonl(root / CHILDREN_FILE, {"event": "stale_cleaned", "pids": killed, "target_pids": target_pids, "at": updated["finished_at"]})
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


def start_stdin_writer(proc: subprocess.Popen[str], prompt_text: str) -> threading.Thread:
    def write_prompt() -> None:
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(prompt_text)
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
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
            append_jsonl(self.output_root / RESULTS_FILE, dict(result))

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
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    tmp_path.replace(path)


def _reset_run_logs(output_root: Path, *, keep_children: bool) -> None:
    for name in (RESULTS_FILE, SUMMARY_FILE):
        path = output_root / name
        if path.exists():
            path.unlink()
    children_path = output_root / CHILDREN_FILE
    if children_path.exists() and not keep_children:
        children_path.unlink()
