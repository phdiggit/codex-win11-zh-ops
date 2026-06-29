from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_win11_zh.review_pack import (
    PACKAGE_SECTIONS,
    ReviewSnapshot,
    apply_review_pack_to_pr,
    build_review_pack_data,
    check_pr_body_protocol,
    classify_scope,
    format_missing_scope_profile,
    load_command_summary,
    load_review_pack_config,
    render_review_pack,
    splice_review_pack_into_body,
    summarize_validation_metadata,
    update_review_pack_for_apply,
    write_review_pack,
)


GOOD_PR_BODY = """# Summary

Update review package.

# Scope

- README.md

# Validation

Done.

# Risk

No dangerous actions.

# Unresolved

None.

Refs #1
"""


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

        self.assertEqual("unclassified", result["scope_verdict"])
        self.assertEqual(["README.md"], result["in_scope"])
        self.assertIn("no scope profile", result["note"])

    def test_missing_scope_profile_diagnostic_lists_available_profiles(self) -> None:
        config = {"scope_profiles": {"docs": {"allow": ["docs/**"]}, "tests": {"allow": ["tests/**"]}}}

        message = format_missing_scope_profile("governance", config)

        self.assertIn("governance", message)
        self.assertIn("docs, tests", message)
        self.assertIn("omit --scope-profile", message)

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
            path.write_text(
                json.dumps(
                    {
                        "commands": [{"command": "python -m unittest", "result": "passed", "kind": "current_focused"}],
                        "validation": {
                            "current_snapshot": [{"command": "python -m unittest", "result": "passed", "summary": "41 passed"}],
                            "base_snapshot": [{"command": "python -m unittest", "result": "failed", "summary": "old failure"}],
                            "historical": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = load_command_summary(path)

            self.assertEqual(str(path), summary["source"])
            self.assertEqual("passed", summary["validation_summary"]["validation_summary"])
            self.assertEqual("listed", summary["validation_summary"]["fixed_baseline_failures"])

    def test_validation_metadata_without_current_snapshot_stays_unknown_for_fixed_baseline(self) -> None:
        summary = summarize_validation_metadata({"validation": {"historical": [{"command": "old", "result": "failed"}]}})

        self.assertEqual("unknown", summary["validation_summary"])
        self.assertEqual("unknown", summary["fixed_baseline_failures"])

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
        self.assertIn("- head_status_at_generation: `current`", markdown)
        self.assertIn("- head_status_after_apply: `unknown`", markdown)
        self.assertIn("- validation_summary: `unknown`", markdown)
        self.assertIn("## Required Next Actions", markdown)
        self.assertIn("Review project-specific findings manually.", markdown)
        self.assertIn("merge_judgment: `not_provided_by_tool`", markdown)

    def test_update_review_pack_for_apply_marks_head_current(self) -> None:
        package = "# Codex PR Review Package\n\n## Reviewer Quick Summary\n\n- head_status_at_generation: `unknown`\n- head_status_after_apply: `unknown`\n\n## Commands Run\n\n- validation_summary: `unknown`\n\n```json\n{}\n```\n"

        updated = update_review_pack_for_apply(package)

        self.assertIn("- head_status_at_generation: `unknown`", updated)
        self.assertIn("- head_status_after_apply: `current`", updated)

    def test_update_review_pack_for_apply_rewrites_command_log_summary_and_json(self) -> None:
        package = "# Codex PR Review Package\n\n## Reviewer Quick Summary\n\n- head_status_at_generation: `unknown`\n- head_status_after_apply: `unknown`\n- validation_summary: `unknown`\n- pr_induced_failures: `unknown`\n- fixed_baseline_failures: `unknown`\n\n## Commands Run\n\n- validation_summary: `unknown`\n\n```json\n{\"validation_summary\": \"unknown\"}\n```\n\n## Protocol Compliance\n\n- unknown\n"
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "commands.json"
            log_path.write_text(
                json.dumps(
                    {
                        "validation": {
                            "current_snapshot": [{"command": "focused", "result": "passed", "summary": "3 passed"}],
                            "base_snapshot": [{"command": "old base", "result": "failed", "summary": "known baseline"}],
                        }
                    }
                ),
                encoding="utf-8",
            )

            updated = update_review_pack_for_apply(package, command_log=log_path)

        self.assertIn("- validation_summary: `passed`", updated)
        self.assertIn("- fixed_baseline_failures: `listed`", updated)
        self.assertIn('"validation_summary": "passed"', updated)
        self.assertNotIn('"validation_summary": "unknown"', updated)

    def test_update_review_pack_for_apply_syncs_manual_validation_metadata_to_json(self) -> None:
        package = "# Codex PR Review Package\n\n## Reviewer Quick Summary\n\n- head_status_at_generation: `unknown`\n- head_status_after_apply: `unknown`\n- validation_summary: `unknown`\n- pr_induced_failures: `unknown`\n- fixed_baseline_failures: `unknown`\n\n## Commands Run\n\n- validation_summary: `pass`\n- pr_induced_failures: `none_known`\n\n```json\n{\"validation_summary\": {\"validation_summary\": \"unknown\", \"pr_induced_failures\": \"unknown\"}}\n```\n\n## Protocol Compliance\n\n- unknown\n"

        updated = update_review_pack_for_apply(package)

        self.assertIn("- validation_summary: `pass`", updated)
        self.assertIn("- pr_induced_failures: `none_known`", updated)
        self.assertIn('"validation_summary": "pass"', updated)
        self.assertIn('"pr_induced_failures": "none_known"', updated)
        self.assertNotIn('"pr_induced_failures": "unknown"', updated)

    def test_splice_review_pack_replaces_existing_package_section(self) -> None:
        body = "# 摘要\n\nKeep me.\n\n# Codex PR Review Package\n\nold\n\n# 尾部\n\nKeep tail.\n"
        package = "# Codex PR Review Package\n\n## Reviewer Quick Summary\n\n- head_status: `current`\n"

        merged = splice_review_pack_into_body(body, package)

        self.assertIn("Keep me.", merged)
        self.assertIn("- head_status: `current`", merged)
        self.assertIn("Keep tail.", merged)
        self.assertNotIn("\nold\n", merged)

    def test_splice_review_pack_replaces_legacy_v11_section_without_duplicate(self) -> None:
        body = "# Summary\n\nKeep me.\n\n## Codex PR Review Package v1.1\n\nold package\n\n## Tail\n\nKeep tail.\n"
        package = "# Codex PR Review Package\n\n## Reviewer Quick Summary\n\n- head_status: `current`\n"

        merged = splice_review_pack_into_body(body, package)

        self.assertIn("Keep me.", merged)
        self.assertIn("Keep tail.", merged)
        self.assertIn("# Codex PR Review Package", merged)
        self.assertNotIn("Codex PR Review Package v1.1", merged)
        self.assertNotIn("old package", merged)
        self.assertEqual(1, merged.count("Codex PR Review Package"))

    def test_apply_review_pack_writes_merged_body_and_verifies_marker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            package_file = Path(td) / "review-pack.md"
            body_file = Path(td) / "body.md"
            package_file.write_text("# Codex PR Review Package\n\n## Reviewer Quick Summary\n\n- head_status: `current`\n\nhead123\n", encoding="utf-8")
            view = {
                "number": 1,
                "url": "https://example/pr/1",
                "body": GOOD_PR_BODY,
                "headRefOid": "head123",
            }

            def fake_apply(*, pr: str, body_file: str | Path, cwd=None, require_sections: bool = True):
                text = Path(body_file).read_text(encoding="utf-8")
                self.assertIn("# Codex PR Review Package", text)
                self.assertIn("- head_status_after_apply: `current`", text)
                self.assertIn("head123", text)
                return {**view, "body": text}

            with patch("codex_win11_zh.review_pack.pr_view", return_value=view), patch(
                "codex_win11_zh.review_pack.pr_body_apply", side_effect=fake_apply
            ):
                result = apply_review_pack_to_pr(pr="1", package_file=package_file, body_file=body_file)

            self.assertEqual("head123", result["head_sha"])
            self.assertTrue(body_file.exists())

    def test_write_review_pack_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".tmp" / "review-pack.md"

            write_review_pack(path, "# Codex PR Review Package\n")

            self.assertEqual("# Codex PR Review Package\n", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
