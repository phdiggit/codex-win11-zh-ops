from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(root: Path) -> list[dict]:
    items = []
    for path in sorted(root.glob("*/scenario.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["path"] = str(path.parent)
        items.append(data)
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent / "scenarios"))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    scenarios = load(Path(args.root))
    report = {
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
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
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
