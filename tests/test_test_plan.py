from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_win11_zh.test_plan import DEFAULT_STATE_FILE, build_test_plan, classify_changed_files, read_changed_files


ROOT = Path(__file__).resolve().parents[1]


class TestPlanTests(unittest.TestCase):
    def test_default_state_file_uses_tmp(self) -> None:
        self.assertEqual(".tmp/codex-test-plan-state.json", DEFAULT_STATE_FILE)

    def test_source_change_requires_full_and_recommends_focused_test(self) -> None:
        data = classify_changed_files(["src/codex_win11_zh/runtime.py"], cwd=ROOT)

        self.assertTrue(data["full_pytest_required"])
        self.assertIn("tests/test_runtime.py", data["focused_tests"])

    def test_recorded_head_blocks_repeated_current_full(self) -> None:
        plan = build_test_plan(
            base="origin/main",
            head="HEAD",
            base_sha="base123",
            head_sha="head123",
            changed_files=["src/codex_win11_zh/runtime.py"],
            state={"current_full": {"head123": {"status": "passed"}}, "base_full": {}},
        )

        self.assertFalse(plan.data["policy"]["current_head_full"]["allowed"])
        self.assertFalse(plan.data["policy"]["base_head_full"]["allowed"])

    def test_failed_current_full_allows_one_base_classification(self) -> None:
        plan = build_test_plan(
            base="origin/main",
            head="HEAD",
            base_sha="base123",
            head_sha="head123",
            changed_files=["src/codex_win11_zh/runtime.py"],
            state={"current_full": {"head123": {"status": "failed"}}, "base_full": {}},
        )

        self.assertTrue(plan.data["policy"]["base_head_full"]["allowed"])

    def test_supplied_current_full_status_blocks_repeated_current_full(self) -> None:
        plan = build_test_plan(
            base="origin/main",
            head="HEAD",
            base_sha="base123",
            head_sha="head123",
            changed_files=["src/codex_win11_zh/runtime.py"],
            state={"current_full": {}, "base_full": {}},
            current_full_status="failed",
        )

        self.assertFalse(plan.data["policy"]["current_head_full"]["allowed"])
        self.assertTrue(plan.data["policy"]["base_head_full"]["allowed"])

    def test_template_only_change_recommends_template_test_without_full(self) -> None:
        data = classify_changed_files(["templates/repo/AGENTS.md"], cwd=ROOT)

        self.assertFalse(data["full_pytest_required"])
        self.assertEqual(["tests/test_templates.py"], data["focused_tests"])

    def test_changed_files_reader_accepts_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "changed.txt"
            path.write_text("\ufeffsrc\\codex_win11_zh\\runtime.py\n", encoding="utf-8")

            self.assertEqual(["src/codex_win11_zh/runtime.py"], read_changed_files(path))


if __name__ == "__main__":
    unittest.main()
