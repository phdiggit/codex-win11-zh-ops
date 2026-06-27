from __future__ import annotations

import json
import sys
from typing import Any

from ..shell import format_issues, lint_command
from ..stdio import configure_utf8_stdio


def _extract_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        value = tool_input.get("command")
        if isinstance(value, str):
            return value
    return ""


def main() -> int:
    configure_utf8_stdio()
    payload: dict[str, Any] = {}
    raw = sys.stdin.read()
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

    command = _extract_command(payload)
    issues = lint_command(command) if command else []
    dangerous = [i for i in issues if i.code == "DANGEROUS_DELETE"]
    if dangerous:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": format_issues(dangerous),
                },
            }
        }, ensure_ascii=False))
        return 0

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest"}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
