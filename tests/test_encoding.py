from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_win11_zh.encoding import read_text_auto, roundtrip_check, write_json_utf8, write_utf8_no_bom


class EncodingTests(unittest.TestCase):
    def test_write_utf8_no_bom(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "中文.md"
            write_utf8_no_bom(path, "你好\n")
            self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            result = read_text_auto(path)
            self.assertEqual(result.encoding, "utf-8")
            self.assertEqual(result.text, "你好\n")

    def test_write_json_utf8_keeps_chinese(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.json"
            write_json_utf8(path, {"name": "示例"})
            text = path.read_text(encoding="utf-8")
            self.assertIn("示例", text)
            self.assertNotIn("\\u793a", text)
            self.assertEqual(json.loads(text)["name"], "示例")

    def test_roundtrip_detects_cp936(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "legacy.txt"
            path.write_bytes("中文".encode("cp936"))
            issues = roundtrip_check(path)
            self.assertTrue(any(i.code == "LEGACY_CP936" for i in issues))


if __name__ == "__main__":
    unittest.main()
