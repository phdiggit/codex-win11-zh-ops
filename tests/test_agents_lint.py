from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_win11_zh.agents_lint import lint_agents_file


class AgentsLintTests(unittest.TestCase):
    def test_minimal_agents_gets_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "AGENTS.md"
            path.write_text("# AGENTS.md\n\n只写一句话。\n", encoding="utf-8")
            issues = lint_agents_file(path)
            self.assertTrue(any(i.code.startswith("MISSING_") for i in issues))


if __name__ == "__main__":
    unittest.main()
