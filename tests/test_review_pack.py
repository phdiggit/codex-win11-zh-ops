from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_win11_zh.review_pack import (
    PACKAGE_SECTIONS,
    ReviewSnapshot,
    build_review_pack_data,
    check_pr_body_protocol,
    classify_scope,
    load_command_summary,
    load_review_pack_config,
    render_review_pack,
    write_review_pack,
)


class ReviewPackTests(unittest.TestCase):
    def test_scope_profile_classifies_forbidden_and_suspicious_files(self) -> None:
        config = {
            "scope_profiles": {
                "data-jsonl": {
                    "allow": ["data/**", "tests/**"],
                    "suspicious": ["docs/**", "exports/**"],
                    "forbid": ["data/configs/**", "project_config.yml"],
                }
            }
        }

        result = classify_scope(
            ["data/items.jsonl", "docs/guide.md", "data/configs/prod.json", "README.md"],
            config=config,
            profile_name="data-jsonl",
        )

        self.assertEqual("blocked", result["scope_verdict"])
        self.assertEqual(["data/items.jsonl"], result["in_scope"])
        self.assertEqual(["docs/guide.md", "README.md"], result["suspicious_or_out_of_scope"])
        self.assertEqual(["data/configs/prod.json"], result["forbidden_hits"])

    def test_scope_without_profile_keeps_fact_layer_broad(self) -> None:
        result = classify_scope(["README.md"], config={}, profile_name=None)

        self.assertEqual("clean", result["scope_verdict"])
        self.assertEqual(["README.md"], result["in_scope"])
        self.assertIn("no scope profile", result["note"])

    def test_protocol_checks_head_sha_and_changed_files(self) -> None:
        body = """# 摘要

head_sha: abcdef123456

# 范围和修改文件

- `README.md`
- `src/codex_win11_zh/review_pack.py`
"""

        protocol = check_pr_body_protocol(
            body,
            head_sha="abcdef123456",
            changed_files=["README.md", "src/codex_win11_zh/review_pack.py"],
        )

        self.assertEqual("pass", protocol["head_sha_matches_current_head"]["status"])
        self.assertEqual("pass", protocol["changed_files_section_present"]["status"])
        self.assertEqual("pass", protocol["changed_files_match_current_diff"]["status"])
        self.assertEqual("pass", protocol["not_obviously_stale"]["status"])

    def test_protocol_reports_unknown_when_pr_body_lacks_structured_facts(self) -> None:
        protocol = check_pr_body_protocol("plain summary only", head_sha="abcdef1", changed_files=["README.md"])

        self.assertEqual("unknown", protocol["head_sha_matches_current_head"]["status"])
        self.assertEqual("unknown", protocol["changed_files_match_current_diff"]["status"])
        self.assertEqual("unknown", protocol["not_obviously_stale"]["status"])

    def test_protocol_does_not_treat_empty_scope_section_as_file_list(self) -> None:
        body = "# Scope\n\nNo file list yet.\n"

        protocol = check_pr_body_protocol(body, head_sha="abcdef1", changed_files=["README.md"])

        self.assertEqual("unknown", protocol["changed_files_section_present"]["status"])
        self.assertEqual("unknown", protocol["changed_files_match_current_diff"]["status"])

    def test_command_log_json_is_embedded_as_fact_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "commands.json"
            path.write_text(json.dumps({"commands": [{"cmd": "python -m unittest", "exit_code": 0}]}), encoding="utf-8")

            summary = load_command_summary(path)

            self.assertEqual(str(path), summary["source"])
            self.assertEqual(0, summary["data"]["commands"][0]["exit_code"])

    def test_load_config_reads_scope_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "review-pack.json"
            path.write_text(json.dumps({"scope_profiles": {"docs": {"allow": ["docs/**"]}}}), encoding="utf-8")

            data = load_review_pack_config(path)

            self.assertIn("docs", data["scope_profiles"])

    def test_render_review_pack_contains_required_sections(self) -> None:
        snapshot = ReviewSnapshot(
            pr_number="3",
            pr_url="https://github.com/example/repo/pull/3",
            base_branch="main",
            head_branch="codex/review-pack",
            base_sha="base123",
            head_sha="head123",
            fetched_at_utc="2026-06-29T00:00:00+00:00",
            fetched_at_local="2026-06-29T08:00:00+08:00",
            diff_source="unit-test",
            changed_files=["README.md"],
            pr_body="",
        )
        scope = classify_scope(["README.md"], config={}, profile_name=None)
        protocol = check_pr_body_protocol("# Changed files\n\n- `README.md`\nhead_sha: head123\n", head_sha="head123", changed_files=["README.md"])
        data = build_review_pack_data(
            snapshot,
            scope=scope,
            protocol=protocol,
            commands={"source": "unit-test", "commands": []},
        )

        markdown = render_review_pack(data)

        for section in PACKAGE_SECTIONS:
            self.assertIn(section, markdown)
        self.assertIn("merge_judgment: `not_provided_by_tool`", markdown)

    def test_write_review_pack_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".tmp" / "review-pack.md"

            write_review_pack(path, "# Codex PR Review Package\n")

            self.assertEqual("# Codex PR Review Package\n", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
