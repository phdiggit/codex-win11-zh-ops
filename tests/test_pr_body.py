from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_win11_zh.cli import main
from codex_win11_zh.pr_body import code_fences_balanced, compare_body, normalize_file, validate_file, validate_text


GOOD = """# 摘要\n\n修复中文正文。\n\n# 范围和修改文件\n\n- README.md\n\n# 验证\n\n```powershell\npython -m unittest discover -s tests\n```\n\n# 风险\n\n未执行危险操作。\n\n# 未解决事项\n\n无。\n\nRefs #1\n"""


class PrBodyTests(unittest.TestCase):
    def test_good_body_validates(self) -> None:
        issues = validate_text(GOOD)
        self.assertEqual([], issues)

    def test_unbalanced_code_fence(self) -> None:
        self.assertFalse(code_fences_balanced("```powershell\nGet-ChildItem\n"))
        issues = validate_text("# 摘要\n\n```powershell\nGet-ChildItem\n", require_sections=False)
        self.assertTrue(any(i.code == "UNBALANCED_CODE_FENCE" for i in issues))

    def test_normalize_file_writes_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "draft.md"
            dst = Path(td) / "body.md"
            src.write_bytes(GOOD.replace("\n", "\r\n").encode("utf-8"))
            normalize_file(src, dst)
            self.assertEqual(GOOD, dst.read_text(encoding="utf-8"))
            self.assertEqual([], validate_file(dst))

    def test_compare_body(self) -> None:
        self.assertEqual([], compare_body(GOOD, GOOD.rstrip("\n")))
        issues = compare_body(GOOD, GOOD.replace("中文", "乱码"))
        self.assertTrue(any(i.code == "REMOTE_BODY_MISMATCH" for i in issues))

    def test_body_cli_alias_normalizes_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "comment-draft.md"
            dst = Path(td) / "comment-body.md"
            src.write_bytes(GOOD.replace("\n", "\r\n").encode("utf-8"))

            self.assertEqual(0, main(["body", "normalize", "--input", str(src), "--output", str(dst)]))
            self.assertEqual(0, main(["body", "validate", str(dst)]))
            self.assertEqual(GOOD, dst.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
