from __future__ import annotations

import unittest

from codex_win11_zh.shell import lint_command


class ShellTests(unittest.TestCase):
    def test_ps51_chain_operator_error(self) -> None:
        issues = lint_command("git status && git diff", shell="powershell5")
        self.assertTrue(any(i.code == "PS51_CHAIN_OPERATOR" and i.severity == "error" for i in issues))

    def test_bash_allows_chain_operator(self) -> None:
        issues = lint_command("git status && git diff", shell="bash")
        self.assertFalse(any(i.code == "PS51_CHAIN_OPERATOR" for i in issues))

    def test_gh_inline_chinese_body_error(self) -> None:
        issues = lint_command('gh pr create --body "摘要：中文正文"', shell="powershell5")
        self.assertTrue(any(i.code == "GH_INLINE_BODY_RISK" for i in issues))

    def test_dangerous_delete_warning(self) -> None:
        issues = lint_command("git clean -fd", shell="powershell5")
        self.assertTrue(any(i.code == "DANGEROUS_DELETE" for i in issues))


if __name__ == "__main__":
    unittest.main()
