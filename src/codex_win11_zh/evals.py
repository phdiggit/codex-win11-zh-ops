from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Scenario:
    name: str
    path: Path
    goal: str
    checks: list[str]


def scenario_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    # Running from source tree.
    return Path(__file__).resolve().parents[2] / "evals" / "scenarios"


def load_scenarios(root: str | Path | None = None) -> list[Scenario]:
    base = scenario_root(root)
    scenarios: list[Scenario] = []
    if not base.exists():
        return scenarios
    for meta_path in sorted(base.glob("*/scenario.json")):
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        scenarios.append(
            Scenario(
                name=data.get("name") or meta_path.parent.name,
                path=meta_path.parent,
                goal=data.get("goal", ""),
                checks=list(data.get("checks", [])),
            )
        )
    return scenarios


def build_report(root: str | Path | None = None) -> dict[str, Any]:
    scenarios = load_scenarios(root)
    return {
        "schema": "codex-win11-zh-ops.eval-report.v1",
        "scenario_count": len(scenarios),
        "scenarios": [
            {"name": s.name, "path": str(s.path), "goal": s.goal, "checks": s.checks}
            for s in scenarios
        ],
        "metrics_to_collect": [
            "tool_call_count",
            "failed_command_count",
            "retry_count",
            "used_gh_cli",
            "used_connector_fallback",
            "ps51_syntax_error",
            "mojibake_detected",
            "pr_body_verified",
            "changed_files_in_scope",
        ],
    }
