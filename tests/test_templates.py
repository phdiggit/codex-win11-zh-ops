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
            rc = main(["install-template", "--profile", "strict", "--target", str(target), "--hooks"])

            self.assertEqual(0, rc)
            self.assertTrue((target / "AGENTS.md").exists())
            self.assertTrue((target / ".gitattributes").exists())
            self.assertTrue((target / "docs" / "codex-workflow.md").exists())
            self.assertTrue((target / "docs" / "codex-task-card-template.md").exists())
            self.assertTrue((target / ".codex" / "hooks.json").exists())


if __name__ == "__main__":
    unittest.main()
