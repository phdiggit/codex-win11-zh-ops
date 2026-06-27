from __future__ import annotations

import json
import sys
from typing import Any

from ..shell import format_issues, lint_command
from ..stdio import configure_utf8_stdio


def _extract_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    for key in ("command", "cmd"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _extract_shell(payload: dict[str, Any]) -> str | None:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("shell", "executable"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value
    value = payload.get("shell")
    return value if isinstance(value, str) else None


def deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def context(message: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    }


def main() -> int:
    configure_utf8_stdio()
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps(context("pre_tool_use hook 收到非 JSON 输入，未执行策略。"), ensure_ascii=False))
        return 0

    command = _extract_command(payload)
    if not command:
        print(json.dumps(context("pre_tool_use hook 未发现 command 字段，未执行 shell 策略。"), ensure_ascii=False))
        return 0

    issues = lint_command(command, shell=_extract_shell(payload))
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        print(json.dumps(deny(format_issues(errors)), ensure_ascii=False))
        return 0

    warnings = [i for i in issues if i.severity == "warning"]
    if warnings:
        print(json.dumps(context(format_issues(warnings)), ensure_ascii=False))
        return 0

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse"}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
