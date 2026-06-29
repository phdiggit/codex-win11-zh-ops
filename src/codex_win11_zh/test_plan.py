from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git import run_git


DEFAULT_STATE_FILE = ".tmp/codex-test-plan-state.json"
FULL_STATUS_VALUES = {"passed", "failed"}


@dataclass(frozen=True)
class TestPlan:
    data: dict[str, Any]

    @property
    def summary(self) -> str:
        return str(self.data["summary"])


def read_changed_files(path: str | Path) -> list[str]:
    return [
        line.strip().lstrip("\ufeff").replace("\\", "/")
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def git_changed_files(base: str, head: str, *, cwd: str | Path | None = None) -> list[str]:
    result = run_git(["diff", "--name-only", f"{base}...{head}"], cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git diff failed for {base}...{head}")
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def resolve_ref(ref: str, *, cwd: str | Path | None = None) -> str:
    result = run_git(["rev-parse", ref], cwd=cwd)
    if result.returncode != 0:
        return ref
    return result.stdout.strip() or ref


def load_state(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"current_full": {}, "base_full": {}}
    state_path = Path(path)
    if not state_path.exists():
        return {"current_full": {}, "base_full": {}}
    data = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("test plan state must be a JSON object")
    data.setdefault("current_full", {})
    data.setdefault("base_full", {})
    return data


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_full_result(
    state: dict[str, Any],
    *,
    kind: str,
    sha: str,
    status: str,
) -> None:
    if status not in FULL_STATUS_VALUES:
        raise ValueError(f"unknown full test status: {status}")
    if kind not in {"current_full", "base_full"}:
        raise ValueError(f"unknown full test kind: {kind}")
    state.setdefault(kind, {})[sha] = {"status": status}


def classify_changed_files(changed_files: list[str], *, cwd: str | Path | None = None) -> dict[str, Any]:
    if not changed_files:
        return {
            "full_pytest_required": False,
            "reasons": ["no changed files"],
            "focused_tests": [],
            "focused_commands": [],
        }

    root = Path(cwd) if cwd is not None else Path.cwd()
    full_required = False
    reasons: list[str] = []
    focused_tests: set[str] = set()

    module_to_test = {
        "agents_lint.py": "tests/test_agents_lint.py",
        "cleanup.py": "tests/test_cleanup.py",
        "encoding.py": "tests/test_encoding.py",
        "hooks/pre_tool_use.py": "tests/test_hooks.py",
        "pr_body.py": "tests/test_pr_body.py",
        "runtime.py": "tests/test_runtime.py",
        "shell.py": "tests/test_shell.py",
        "test_plan.py": "tests/test_test_plan.py",
    }

    for rel in changed_files:
        rel = rel.replace("\\", "/")
        if rel in {"pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "pytest.ini"} or rel.startswith(".github/workflows/"):
            full_required = True
            reasons.append(f"test or package configuration changed: {rel}")
        elif rel.startswith("src/") and rel.endswith(".py"):
            full_required = True
            reasons.append(f"python source changed: {rel}")
            src_rel = rel.removeprefix("src/codex_win11_zh/")
            mapped = module_to_test.get(src_rel)
            if mapped and (root / mapped).exists():
                focused_tests.add(mapped)
        elif rel.startswith("tests/") and rel.endswith(".py"):
            if Path(rel).name.startswith("test_"):
                focused_tests.add(rel)
            else:
                full_required = True
                reasons.append(f"shared test support changed: {rel}")
        elif rel.startswith("templates/") or rel.startswith("src/codex_win11_zh/templates/"):
            mapped = "tests/test_templates.py"
            if (root / mapped).exists():
                focused_tests.add(mapped)
        elif rel.startswith("src/codex_win11_zh/hooks/"):
            mapped = "tests/test_hooks.py"
            if (root / mapped).exists():
                focused_tests.add(mapped)

    focused = sorted(focused_tests)
    return {
        "full_pytest_required": full_required,
        "reasons": reasons or ["focused validation is enough for the changed files"],
        "focused_tests": focused,
        "focused_commands": [f"python -m pytest {path}" for path in focused],
    }


def build_test_plan(
    *,
    base: str,
    head: str,
    changed_files: list[str],
    base_sha: str,
    head_sha: str,
    state: dict[str, Any] | None = None,
    current_full_status: str | None = None,
) -> TestPlan:
    state = state or {"current_full": {}, "base_full": {}}
    classification = classify_changed_files(changed_files)
    head_record = state.get("current_full", {}).get(head_sha)
    base_record = state.get("base_full", {}).get(base_sha)
    effective_current_status = current_full_status or (head_record or {}).get("status")

    full_required = bool(classification["full_pytest_required"])
    current_seen = head_record is not None or current_full_status is not None
    current_allowed = full_required and not current_seen
    if not full_required:
        current_reason = "full pytest is not required for this change set"
    elif current_full_status is not None:
        current_reason = f"full pytest status was supplied for head {head_sha}"
    elif head_record is not None:
        current_reason = f"full pytest already recorded for head {head_sha}"
    else:
        current_reason = f"full pytest has not been recorded for head {head_sha}"

    base_allowed = full_required and effective_current_status == "failed" and base_record is None
    if effective_current_status != "failed":
        base_reason = "base full pytest is only allowed after current-head full pytest fails"
    elif base_record is not None:
        base_reason = f"base full pytest already recorded for {base_sha}"
    else:
        base_reason = "current-head full pytest failed; base run may classify baseline"

    focused = classification["focused_tests"]
    if full_required:
        summary = "Full pytest required; run focused tests first when listed, then at most one current-head full run."
    elif focused:
        summary = "Focused tests are sufficient unless new risk appears."
    else:
        summary = "No pytest run is required by the current changed-file classification."

    return TestPlan(
        {
            "base": base,
            "head": head,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "changed_files": changed_files,
            "classification": classification,
            "policy": {
                "current_head_full": {
                    "allowed": current_allowed,
                    "recorded": head_record,
                    "reason": current_reason,
                },
                "base_head_full": {
                    "allowed": base_allowed,
                    "recorded": base_record,
                    "reason": base_reason,
                },
            },
            "summary": summary,
        }
    )
