from __future__ import annotations

import json
import os
import subprocess
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from codex_win11_zh.agent import is_process_running
from codex_win11_zh.cli import main


FAKE_CODEX = r"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time


def arg_value(name: str) -> str:
    if name not in sys.argv:
        return ""
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return ""
    return sys.argv[index + 1]


prompt = sys.stdin.read() if "-" in sys.argv else ""
last_message = pathlib.Path(arg_value("--output-last-message"))
if last_message:
    last_message.parent.mkdir(parents=True, exist_ok=True)
task_code = last_message.name.split(".last", 1)[0] if last_message else ""

if "SPAWN_CHILD=" in prompt:
    child_file = pathlib.Path(prompt.split("SPAWN_CHILD=", 1)[1].splitlines()[0])
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    child_file.write_text(str(child.pid), encoding="utf-8")
    time.sleep(30)
elif "spawn_child" in task_code:
    child_file = last_message.parent.parent / "child.pid"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    child_file.write_text(str(child.pid), encoding="utf-8")
    time.sleep(30)

if "SLEEP" in prompt or "timeout" in task_code:
    time.sleep(5)

if last_message and "LAST_MESSAGE_PATCH" in prompt:
    last_message.write_text(
        "```jsonl\n" + json.dumps({"kind": "fallback_patch"}, ensure_ascii=False) + "\n```\n",
        encoding="utf-8",
    )
elif last_message:
    last_message.write_text(json.dumps({"ok": True, "chars": len(prompt)}, ensure_ascii=False), encoding="utf-8")

if "NO_PATCH" not in prompt and "PATCH_PATH=" in prompt:
    patch_path = pathlib.Path(prompt.split("PATCH_PATH=", 1)[1].splitlines()[0])
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(json.dumps({"kind": "fake_patch"}, ensure_ascii=False) + "\n", encoding="utf-8")
elif "NO_PATCH" not in prompt and task_code and "fail" not in task_code and "timeout" not in task_code and "bad_json" not in task_code:
    patch_path = last_message.parent.parent / "patches" / f"{task_code}.jsonl"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(json.dumps({"kind": "fake_patch"}, ensure_ascii=False) + "\n", encoding="utf-8")

if "TURN_BROKEN" in prompt:
    print(json.dumps({"type": "turn.failed", "message": "You've hit your usage limit. Please try again later."}))
elif "USAGE_ERROR" in prompt:
    print(json.dumps({"type": "error", "message": "rate limit exceeded"}))
elif "BADJSON" in prompt or "bad_json" in task_code:
    print("not-json")
else:
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 3}}))

if "FAIL" in prompt or "fail" in task_code:
    raise SystemExit(3)
"""


class AgentCliTests(unittest.TestCase):
    def test_run_plan_executes_tasks_and_collects_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks = [
                make_task(root, "task_a", "PATCH_PATH=patches/task_a.jsonl\n"),
                make_task(root, "task_b", "PATCH_PATH=patches/task_b.jsonl\n"),
            ]
            tasks_jsonl = write_tasks(root, tasks)
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--max-workers",
                    "2",
                    "--timeout-seconds",
                    "5",
                ]
            )

            self.assertEqual(0, rc)
            status = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual("succeeded", status["status"])
            self.assertEqual({"succeeded": 2}, status["totals"])
            results = read_jsonl(output_root / "results.jsonl")
            self.assertEqual(["succeeded", "succeeded"], [row["status"] for row in results])
            self.assertEqual({"input_tokens": 2, "output_tokens": 3}, results[0]["usage"])
            last_message = json.loads((root / "logs" / "task_a.last.md").read_text(encoding="utf-8"))
            self.assertGreater(last_message["chars"], 0)

            collect_rc = main(["agent", "collect", "--output-root", str(output_root)])

            self.assertEqual(0, collect_rc)
            summary = json.loads((output_root / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["ok"])
            self.assertEqual(2, summary["totals"]["tasks"])

    def test_run_plan_rejects_duplicate_task_codes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks_jsonl = write_tasks(root, [make_task(root, "dup", ""), make_task(root, "dup", "")])

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(root / "agent_run"),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                ]
            )

            self.assertEqual(2, rc)

    def test_run_plan_reports_failures_timeouts_and_bad_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks = [
                make_task(root, "fail_task", "FAIL\n"),
                make_task(root, "timeout_task", "SLEEP\n"),
                make_task(root, "bad_json_task", "BADJSON\n"),
            ]
            tasks_jsonl = write_tasks(root, tasks)
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--max-workers",
                    "3",
                    "--timeout-seconds",
                    "1",
                ]
            )

            self.assertEqual(0, rc)
            results = {row["task_code"]: row for row in read_jsonl(output_root / "results.jsonl")}
            self.assertEqual("failed", results["fail_task"]["status"])
            self.assertEqual("timed_out", results["timeout_task"]["status"])
            self.assertEqual("failed", results["bad_json_task"]["status"])
            self.assertEqual("missing_expected_output", results["bad_json_task"]["error_type"])

            collect_rc = main(["agent", "collect", "--output-root", str(output_root)])

            self.assertEqual(1, collect_rc)
            summary = json.loads((output_root / "summary.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["ok"])
            self.assertIn("invalid_jsonl", {issue["code"] for issue in summary["issues"]})

    def test_turn_failed_event_marks_task_failed_even_with_zero_returncode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks_jsonl = write_tasks(root, [make_task(root, "event_limit_task", "PATCH_PATH=patches/event_limit_task.jsonl\nTURN_BROKEN\n")])
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--timeout-seconds",
                    "5",
                ]
            )

            self.assertEqual(0, rc)
            status = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual("failed", status["status"])
            results = read_jsonl(output_root / "results.jsonl")
            self.assertEqual("failed", results[0]["status"])
            self.assertEqual(0, results[0]["returncode"])
            self.assertEqual("usage_limit", results[0]["error_type"])
            self.assertTrue(results[0]["event_analysis"]["failed"])

    def test_missing_declared_patch_marks_task_failed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks_jsonl = write_tasks(root, [make_task(root, "missing_patch_task", "NO_PATCH\n")])
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--timeout-seconds",
                    "5",
                ]
            )

            self.assertEqual(0, rc)
            results = read_jsonl(output_root / "results.jsonl")
            self.assertEqual("failed", results[0]["status"])
            self.assertEqual("missing_expected_output", results[0]["error_type"])
            self.assertFalse((root / "patches" / "missing_patch_task.jsonl").exists())

    def test_patch_can_be_recovered_from_last_message_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            task = make_task(root, "fallback_patch_task", "NO_PATCH\nLAST_MESSAGE_PATCH\n")
            task["patch_fallback_from_last_message"] = True
            tasks_jsonl = write_tasks(root, [task])
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--timeout-seconds",
                    "5",
                ]
            )

            self.assertEqual(0, rc)
            results = read_jsonl(output_root / "results.jsonl")
            self.assertEqual("succeeded", results[0]["status"])
            self.assertTrue(results[0]["output_analysis"]["recoveries"][0]["ok"])
            patch_rows = read_jsonl(root / "patches" / "fallback_patch_task.jsonl")
            self.assertEqual("fallback_patch", patch_rows[0]["kind"])

    def test_expected_output_path_contract_marks_task_failed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            task = make_task(root, "expected_output_task", "PATCH_PATH=patches/expected_output_task.jsonl\n")
            task["expected_output_path"] = "reports/expected.jsonl"
            task["expected_min_bytes"] = 5
            tasks_jsonl = write_tasks(root, [task])
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--timeout-seconds",
                    "5",
                ]
            )

            self.assertEqual(0, rc)
            results = read_jsonl(output_root / "results.jsonl")
            self.assertEqual("failed", results[0]["status"])
            self.assertEqual("missing_expected_output", results[0]["error_type"])
            self.assertEqual("expected_output", results[0]["output_analysis"]["checks"][1]["label"])

    def test_background_run_can_be_waited(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks_jsonl = write_tasks(root, [make_task(root, "task_bg", "PATCH_PATH=patches/task_bg.jsonl\n")])
            output_root = root / "agent_run"

            start_rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--background",
                    "--timeout-seconds",
                    "5",
                ]
            )
            wait_rc = main(["agent", "wait", "--output-root", str(output_root), "--timeout-seconds", "10", "--poll-seconds", "0.2"])

            self.assertEqual(0, start_rc)
            self.assertEqual(0, wait_rc)
            status = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual("succeeded", status["status"])
            last_message = json.loads((root / "logs" / "task_bg.last.md").read_text(encoding="utf-8"))
            self.assertGreater(last_message["chars"], 0)

    def test_parallel_status_writes_do_not_share_temp_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks = [make_task(root, f"parallel_{index}", f"PATCH_PATH=patches/parallel_{index}.jsonl\n") for index in range(12)]
            tasks_jsonl = write_tasks(root, tasks)
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--max-workers",
                    "4",
                    "--timeout-seconds",
                    "10",
                ]
            )

            self.assertEqual(0, rc)
            status = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual("succeeded", status["status"])
            self.assertEqual({"succeeded": 12}, status["totals"])
            self.assertFalse(list(output_root.glob("status.json.*.tmp")))

    def test_background_kill_stops_running_supervisor_and_task(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks_jsonl = write_tasks(root, [make_task(root, "long_task", "SLEEP\n")])
            output_root = root / "agent_run"

            start_rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    fake.name,
                    "--background",
                    "--timeout-seconds",
                    "30",
                ]
            )
            running = wait_for_status(output_root, lambda data: any(task.get("status") == "running" for task in data.get("tasks", [])))
            tracked_pids = [pid for pid in [running.get("supervisor_pid"), running["tasks"][0].get("pid")] if isinstance(pid, int)]

            kill_rc = main(["agent", "kill", "--output-root", str(output_root)])

            self.assertEqual(0, start_rc)
            self.assertEqual(0, kill_rc)
            killed = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual("killed", killed["status"])
            self.assertTrue(killed["killed_pids"])
            self.assertTrue(set(killed["killed_pids"]).issubset(set(killed["target_pids"])))
            self.assertEqual("killed", killed["tasks"][0]["status"])
            for pid in tracked_pids:
                self.assertFalse(is_process_running(pid), f"pid should be stopped: {pid}")

    def test_cleanup_stale_does_not_kill_live_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks_jsonl = write_tasks(root, [make_task(root, "live_cleanup_task", "SLEEP\n")])
            output_root = root / "agent_run"

            start_rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--background",
                    "--timeout-seconds",
                    "30",
                ]
            )
            running = wait_for_status(output_root, lambda data: any(task.get("status") == "running" for task in data.get("tasks", [])))

            cleanup_rc = main(["agent", "cleanup-stale", "--output-root", str(output_root)])
            after_cleanup = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
            supervisor_alive_before_kill = is_process_running(running["supervisor_pid"])
            kill_rc = main(["agent", "kill", "--output-root", str(output_root)])

            self.assertEqual(0, start_rc)
            self.assertEqual(0, cleanup_rc)
            self.assertEqual("running", after_cleanup["status"])
            self.assertTrue(supervisor_alive_before_kill)
            self.assertEqual(0, kill_rc)

    def test_cleanup_stale_kills_recorded_running_pid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output_root = root / "agent_run"
            output_root.mkdir()
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                status = {
                    "schema": "codex-win.agent.status.v1",
                    "status": "running",
                    "supervisor_pid": 999999999,
                    "tasks": [{"task_code": "stale_task", "status": "running", "pid": proc.pid}],
                }
                (output_root / "status.json").write_text(json.dumps(status), encoding="utf-8")
                (output_root / "children.jsonl").write_text(json.dumps({"event": "task_started", "pid": proc.pid}) + "\n", encoding="utf-8")

                cleanup_rc = main(["agent", "cleanup-stale", "--output-root", str(output_root)])

                self.assertEqual(0, cleanup_rc)
                updated = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
                self.assertEqual("stale_cleaned", updated["status"])
                self.assertIn(proc.pid, updated["killed_pids"])
                self.assertEqual("stale_cleaned", updated["tasks"][0]["status"])
                self.assertFalse(is_process_running(proc.pid))
            finally:
                if is_process_running(proc.pid):
                    proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)

    def test_dry_run_collect_skips_output_contract_checks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks_jsonl = write_tasks(root, [make_task(root, "planned_task", "")])
            output_root = root / "agent_run"

            run_rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--dry-run",
                ]
            )
            bad_log = root / "logs" / "planned_task.jsonl"
            bad_log.parent.mkdir(parents=True, exist_ok=True)
            bad_log.write_text("not-json\n", encoding="utf-8")

            collect_rc = main(["agent", "collect", "--output-root", str(output_root)])

            self.assertEqual(0, run_rc)
            self.assertEqual(0, collect_rc)
            summary = json.loads((output_root / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["ok"])
            self.assertNotIn("invalid_jsonl", {issue["code"] for issue in summary["issues"]})

    def test_relative_codex_bin_is_resolved_against_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks_jsonl = write_tasks(root, [make_task(root, "relative_bin_task", "hello relative stdin\n")])
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    fake.name,
                    "--timeout-seconds",
                    "5",
                ]
            )

            self.assertEqual(0, rc)
            status = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual("succeeded", status["status"])
            results = read_jsonl(output_root / "results.jsonl")
            self.assertGreater(results[0]["prompt_bytes"], 0)
            self.assertTrue(results[0]["stdin"]["written"])
            self.assertEqual("read-only", results[0]["command_info"]["actual_sandbox"])
            last_message = json.loads((root / "logs" / "relative_bin_task.last.md").read_text(encoding="utf-8"))
            self.assertGreater(last_message["chars"], 0)

    def test_respect_task_argv_resolves_relative_executable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            task = make_task(root, "respect_relative_task", "hello respect stdin\n")
            task["argv"] = [fake.name, "--output-last-message", task["last_message_path"], "--json", "-"]
            tasks_jsonl = write_tasks(root, [task])
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--respect-task-argv",
                    "--timeout-seconds",
                    "5",
                ]
            )

            self.assertEqual(0, rc)
            status = json.loads((output_root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual("succeeded", status["status"])
            results = read_jsonl(output_root / "results.jsonl")
            self.assertGreater(results[0]["prompt_bytes"], 0)
            self.assertTrue(results[0]["stdin"]["written"])
            self.assertTrue(results[0]["command_info"]["respect_task_argv"])
            last_message = json.loads((root / "logs" / "respect_relative_task.last.md").read_text(encoding="utf-8"))
            self.assertGreater(last_message["chars"], 0)

    def test_respect_task_argv_adds_stdin_marker_for_codex_exec(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            task = make_task(root, "respect_needs_stdin_task", "hello implicit stdin\n")
            task["argv"] = [fake.name, "exec", "-s", "workspace-write", "--output-last-message", task["last_message_path"], "--json"]
            tasks_jsonl = write_tasks(root, [task])
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--respect-task-argv",
                    "--timeout-seconds",
                    "5",
                ]
            )

            self.assertEqual(0, rc)
            results = read_jsonl(output_root / "results.jsonl")
            self.assertEqual("succeeded", results[0]["status"])
            self.assertTrue(results[0]["command_info"]["respect_task_argv_adjusted"])
            self.assertEqual("workspace-write", results[0]["command_info"]["actual_sandbox"])
            self.assertIn(str(root / "tmp"), results[0]["command_info"]["additional_writable_dirs"])
            last_message = json.loads((root / "logs" / "respect_needs_stdin_task.last.md").read_text(encoding="utf-8"))
            self.assertGreater(last_message["chars"], 0)

    def test_local_write_adds_tmp_as_writable_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            task = make_task(root, "local_write_task", "PATCH_PATH=tmp/patches/local_write_task.jsonl\n")
            task["patch_path"] = "tmp/patches/local_write_task.jsonl"
            tasks_jsonl = write_tasks(root, [task])
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--sandbox-profile",
                    "local-write",
                    "--timeout-seconds",
                    "5",
                ]
            )

            self.assertEqual(0, rc)
            results = read_jsonl(output_root / "results.jsonl")
            self.assertEqual("workspace-write", results[0]["command_info"]["actual_sandbox"])
            self.assertIn(str(root / "tmp"), results[0]["command_info"]["additional_writable_dirs"])

    def test_timeout_kills_spawned_child_process(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = make_fake_codex(root)
            tasks_jsonl = write_tasks(root, [make_task(root, "spawn_child_timeout", "")])
            output_root = root / "agent_run"

            rc = main(
                [
                    "agent",
                    "run-plan",
                    "--tasks-jsonl",
                    str(tasks_jsonl),
                    "--output-root",
                    str(output_root),
                    "--cwd",
                    str(root),
                    "--codex-bin",
                    str(fake),
                    "--timeout-seconds",
                    "1",
                ]
            )

            self.assertEqual(0, rc)
            child_pid = int((root / "child.pid").read_text(encoding="utf-8"))
            self.assertFalse(is_process_running(child_pid))
            results = read_jsonl(output_root / "results.jsonl")
            self.assertEqual("timed_out", results[0]["status"])


def make_fake_codex(root: Path) -> Path:
    fake_py = root / "fake_codex.py"
    fake_py.write_text(textwrap.dedent(FAKE_CODEX).strip() + "\n", encoding="utf-8")
    if os.name == "nt":
        wrapper = root / "fake_codex.cmd"
        wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{fake_py}" %*\r\n', encoding="utf-8")
        return wrapper
    wrapper = root / "fake_codex"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_py}" "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


def make_task(root: Path, task_code: str, prompt: str) -> dict[str, object]:
    prompt_path = root / "prompts" / f"{task_code}.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    return {
        "task_code": task_code,
        "prompt_path": str(prompt_path.relative_to(root)),
        "patch_path": f"patches/{task_code}.jsonl",
        "last_message_path": f"logs/{task_code}.last.md",
        "log_path": f"logs/{task_code}.jsonl",
        "argv": ["codex", "exec", "-"],
    }


def write_tasks(root: Path, tasks: list[dict[str, object]]) -> Path:
    path = root / "codex_tasks.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def wait_for_status(output_root: Path, predicate, timeout_seconds: float = 5.0) -> dict[str, object]:
    deadline = time.perf_counter() + timeout_seconds
    status_path = output_root / "status.json"
    last_status: dict[str, object] = {}
    while time.perf_counter() < deadline:
        if status_path.exists():
            last_status = json.loads(status_path.read_text(encoding="utf-8"))
            if predicate(last_status):
                return last_status
        time.sleep(0.1)
    raise AssertionError(f"status predicate was not satisfied: {last_status}")


if __name__ == "__main__":
    unittest.main()
