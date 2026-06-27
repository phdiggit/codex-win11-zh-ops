from __future__ import annotations

import json
import sys
from typing import Any

from ..stdio import configure_utf8_stdio


def main() -> int:
    configure_utf8_stdio()
    raw = sys.stdin.read()
    try:
        payload: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    response = payload.get("tool_response")
    text = json.dumps(response, ensure_ascii=False) if not isinstance(response, str) else response
    hints: list[str] = []
    if "�" in text:
        hints.append("命令输出包含 U+FFFD replacement character，建议检查编码和中文正文是否已损坏。")
    if "The token '&&'" in text or "不是内部或外部命令" in text:
        hints.append("输出疑似 shell 方言错误。Windows PowerShell 5.1 请不要使用 Bash/PowerShell 7 专属语法。")

    if hints:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(hints),
            }
        }, ensure_ascii=False))
    else:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse"}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
