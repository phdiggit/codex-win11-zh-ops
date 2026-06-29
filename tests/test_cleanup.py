from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from codex_win11_zh.cleanup import apply_generated_cleanup, plan_generated_cleanup
from codex_win11_zh.cli import main


class CleanupTests(unittest.TestCase):
    def test_generated_cleanup_dry_run_does_not_delete_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            generated = target / "exports" / "markdown_views" / "page.md"
            outside = target / "notes.md"
            generated.parent.mkdir(parents=True)
            generated.write_text("generated\n", encoding="utf-8")
            outside.write_text("user work\n", encoding="utf-8")

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                rc = main(["cleanup", "generated", "--profile", "markdown-exports", "--target", str(target)])

            self.assertEqual(0, rc)
            self.assertTrue(generated.exists())
            self.assertTrue(outside.exists())
            data = json.loads(buffer.getvalue())
            self.assertEqual("dry-run", data["mode"])
            self.assertIn({"path": "exports/markdown_views/page.md", "kind": "file"}, data["candidates"])

    def test_generated_cleanup_apply_stays_inside_profile_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            generated = target / "exports" / "markdown_views" / "page.md"
            outside = target / "exports" / "other" / "keep.md"
            generated.parent.mkdir(parents=True)
            outside.parent.mkdir(parents=True)
            generated.write_text("generated\n", encoding="utf-8")
            outside.write_text("user work\n", encoding="utf-8")

            plan = plan_generated_cleanup(target, profile_name="markdown-exports")
            result = apply_generated_cleanup(plan)

            self.assertFalse(generated.exists())
            self.assertTrue(outside.exists())
            self.assertIn("exports/markdown_views/page.md", result["deleted"])

    def test_generated_cleanup_config_can_extend_builtin_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            markdown = target / "exports" / "markdown_views" / "page.md"
            extra = target / "reports" / "generated" / "summary.md"
            markdown.parent.mkdir(parents=True)
            extra.parent.mkdir(parents=True)
            markdown.write_text("generated\n", encoding="utf-8")
            extra.write_text("generated\n", encoding="utf-8")
            config = target / "cleanup.json"
            config.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "project-generated": {
                                "extends": "markdown-exports",
                                "patterns": ["reports/generated/**"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            plan = plan_generated_cleanup(target, profile_name="project-generated", config_path=config)
            rel_paths = {candidate.rel_path for candidate in plan.candidates}

            self.assertIn("exports/markdown_views/page.md", rel_paths)
            self.assertIn("reports/generated/summary.md", rel_paths)


if __name__ == "__main__":
    unittest.main()
