from __future__ import annotations

import json
import subprocess
import sys
import unittest


class HookTests(unittest.TestCase):
    def test_pre_tool_use_denies_ps51_chain_operator(self) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": "git status && git diff", "shell": "powershell5"}}
        proc = subprocess.run(
            [sys.executable, "-m", "codex_win11_zh.hooks.pre_tool_use"],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
