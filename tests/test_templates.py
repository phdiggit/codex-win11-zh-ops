from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_win11_zh.cli import main


ROOT = Path(__file__).resolve().parents[1]


def _template_files(base: Path) -> set[Path]:
    return {path.relative_to(base) for path in base.rglob("*") if path.is_file()}


class TemplateTests(unittest.TestCase):
    def test_root_and_package_templates_match(self) -> None:
        root_templates = ROOT / "templates"
        package_templates = ROOT / "src" / "codex_win11_zh" / "templates"

        root_files = _template_files(root_templates)
        package_files = _template_files(package_templates)
        self.assertEqual(root_files, package_files)

        for rel_path in sorted(root_files):
            self.assertEqual(
                (root_templates / rel_path).read_bytes(),
                (package_templates / rel_path).read_bytes(),
                msg=str(rel_path),
            )

    def test_install_template_copies_repo_guardrails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "target"
            rc = main(["install-template", "--profile", "strict", "--target", str(target)])

            self.assertEqual(0, rc)
            self.assertTrue((target / "AGENTS.md").exists())
            self.assertTrue((target / ".gitattributes").exists())
            self.assertTrue((target / "docs" / "codex-workflow.md").exists())
            self.assertTrue((target / "docs" / "codex-task-card-template.md").exists())
            self.assertTrue((target / ".codex" / "hooks.json").exists())

    def test_install_template_no_hooks_can_disable_strict_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "target"
            rc = main(["install-template", "--profile", "strict", "--target", str(target), "--no-hooks"])

            self.assertEqual(0, rc)
            self.assertFalse((target / ".codex" / "hooks.json").exists())

    def test_agents_places_codex_win_commands_in_task_sections(self) -> None:
        text = (ROOT / "templates" / "repo" / "AGENTS.strict.md").read_text(encoding="utf-8")

        self.assertNotIn("## codex-win 工具优先使用场景", text)
        self.assertIn("## Shell、编码与路径", text)
        self.assertIn("codex-win encoding check", text)
        self.assertIn("codex-win shell lint", text)
        self.assertIn("codex-win run -- <command...>", text)
        self.assertIn("codex-win run --log .tmp/codex-commands.jsonl", text)
        self.assertIn("codex-win cleanup generated", text)
        self.assertIn("codex-win test plan", text)
        self.assertIn("## GitHub、Commit 与 PR", text)
        self.assertIn("codex-win gh preflight", text)
        self.assertIn("codex-win body normalize/validate", text)
        self.assertIn("codex-win pr-body ...", text)
        self.assertIn("codex-win body apply", text)
        self.assertIn("codex-win gh pr-create/pr-edit/pr-verify", text)
        self.assertIn("codex-win review-pack", text)
        self.assertIn("codex-win review-pack apply", text)
        self.assertIn("不要凭感觉写精确分钟数", text)
        self.assertIn("precise timing unavailable", text)
        self.assertIn("codex-win timer start/mark/finish", text)
        self.assertIn("gh --jq", text)
        self.assertIn("codex-win agents lint", text)
        self.assertIn("不静默降级", text)
        self.assertIn("## 子 Agent 与批量任务", text)
        self.assertIn("codex-win agent run-plan", text)
        self.assertIn("--permission-profile tmp-jsonl-review --deny-policy deny-rewrite", text)
        self.assertIn("expected_outputs", text)
        self.assertIn("PATCH_JSONL_BEGIN", text)
        self.assertIn("permission_analysis", text)
        self.assertIn("--git-snapshot minimal", text)
        self.assertIn("--git-snapshot full", text)
        self.assertIn("--git-snapshot none", text)
        self.assertIn("cleanup-stale", text)

    def test_workflow_documents_install_edge_cases(self) -> None:
        text = (ROOT / "templates" / "repo" / "codex-workflow.md").read_text(encoding="utf-8")

        self.assertIn("路径包含空格时必须加引号", text)
        self.assertIn("python -m codex_win11_zh.hooks.pre_tool_use", text)
        self.assertIn("matcher", text)


if __name__ == "__main__":
    unittest.main()
