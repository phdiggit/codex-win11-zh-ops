from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from . import __version__
from .agents_lint import lint_agents_file
from .encoding import read_text_auto, roundtrip_check, write_json_utf8
from .evals import build_report, load_scenarios
from .gh import preflight as gh_preflight, pr_create, pr_edit, pr_view, verify_pr_view
from .pr_body import normalize_file, validate_file
from .shell import format_issues, lint_command
from .stdio import configure_utf8_stdio


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_preflight(args: argparse.Namespace) -> int:
    data = gh_preflight(cwd=args.cwd)
    data["python"] = sys.version.split()[0]
    data["tool_version"] = __version__
    print_json(data)
    return 0 if data.get("preferred_interface") == "gh" else 1


def cmd_encoding_check(args: argparse.Namespace) -> int:
    result = read_text_auto(args.path)
    issues = roundtrip_check(args.path)
    print_json(
        {
            "path": str(result.path),
            "encoding": result.encoding,
            "had_bom": result.had_bom,
            "issues": [issue.__dict__ for issue in issues],
        }
    )
    return 1 if any(i.code in {"REPLACEMENT_CHAR", "POSSIBLE_MOJIBAKE"} for i in issues) else 0


def cmd_encoding_write_json(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    write_json_utf8(args.path, data, sort_keys=args.sort_keys)
    print(f"wrote UTF-8 JSON: {args.path}")
    return 0


def cmd_pr_body_normalize(args: argparse.Namespace) -> int:
    normalize_file(args.input, args.output)
    print(f"normalized PR body: {args.output}")
    return 0


def cmd_pr_body_validate(args: argparse.Namespace) -> int:
    issues = validate_file(args.path, require_sections=not args.no_required_sections)
    if issues:
        for issue in issues:
            print(f"[{issue.code}] {issue.message}")
            if issue.suggestion:
                print(f"  建议：{issue.suggestion}")
        return 1
    print("PR body validation passed")
    return 0


def cmd_gh_preflight(args: argparse.Namespace) -> int:
    data = gh_preflight(cwd=args.cwd, hostname=args.hostname)
    print_json(data)
    return 0 if data.get("preferred_interface") == "gh" else 1


def cmd_gh_pr_view(args: argparse.Namespace) -> int:
    print_json(pr_view(args.pr, cwd=args.cwd))
    return 0


def cmd_gh_pr_create(args: argparse.Namespace) -> int:
    view = pr_create(title=args.title, body_file=args.body_file, base=args.base, head=args.head, draft=args.draft, cwd=args.cwd)
    print_json(view)
    return 0


def cmd_gh_pr_edit(args: argparse.Namespace) -> int:
    draft: bool | None
    if args.draft is None:
        draft = None
    else:
        draft = args.draft.lower() == "true"
    view = pr_edit(pr=args.pr, title=args.title, body_file=args.body_file, base=args.base, head=args.head, draft=draft, cwd=args.cwd)
    print_json(view)
    return 0


def cmd_gh_pr_verify(args: argparse.Namespace) -> int:
    draft = None if args.draft is None else args.draft.lower() == "true"
    view = pr_view(args.pr, cwd=args.cwd)
    verify_pr_view(view, title=args.title, body_file=args.body_file, base=args.base, head=args.head, draft=draft)
    print("PR verification passed")
    return 0


def cmd_shell_lint(args: argparse.Namespace) -> int:
    issues = lint_command(args.command, shell=args.shell)
    if issues:
        print(format_issues(issues))
        return 1 if any(i.severity == "error" for i in issues) else 0
    print("shell command lint passed")
    return 0


def cmd_agents_lint(args: argparse.Namespace) -> int:
    issues = lint_agents_file(args.path, max_lines=args.max_lines)
    if issues:
        for issue in issues:
            print(f"[{issue.severity.upper()}] {issue.code}: {issue.message}")
            if issue.suggestion:
                print(f"  建议：{issue.suggestion}")
        return 1 if any(i.severity == "error" for i in issues) else 0
    print("AGENTS lint passed")
    return 0


def _template_base() -> Path:
    try:
        package_templates = resources.files("codex_win11_zh").joinpath("templates")
        if package_templates.is_dir():
            return Path(str(package_templates))
    except Exception:
        pass
    return Path(__file__).resolve().parents[2] / "templates"


def _copy_file(src: Path, dst: Path, *, overwrite: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not overwrite:
        raise FileExistsError(f"target exists: {dst}")
    shutil.copyfile(src, dst)


def cmd_install_template(args: argparse.Namespace) -> int:
    base = _template_base()
    target = Path(args.target).resolve()
    target.mkdir(parents=True, exist_ok=True)

    profile = args.profile
    repo_dir = base / "repo"
    if profile == "strict":
        root_agents = repo_dir / "AGENTS.strict.md"
    else:
        root_agents = repo_dir / "AGENTS.md"

    _copy_file(root_agents, target / "AGENTS.md", overwrite=args.overwrite)
    _copy_file(repo_dir / ".gitattributes", target / ".gitattributes", overwrite=args.overwrite)
    _copy_file(repo_dir / "docs_AGENTS.md", target / "docs" / "AGENTS.md", overwrite=args.overwrite)
    _copy_file(repo_dir / "scripts_AGENTS.md", target / "scripts" / "AGENTS.md", overwrite=args.overwrite)
    _copy_file(repo_dir / "codex-workflow.md", target / "docs" / "codex-workflow.md", overwrite=args.overwrite)
    _copy_file(repo_dir / "codex-task-card-template.md", target / "docs" / "codex-task-card-template.md", overwrite=args.overwrite)
    if args.hooks:
        _copy_file(base / "hooks" / "hooks.json", target / ".codex" / "hooks.json", overwrite=args.overwrite)

    print(f"installed {profile} template into {target}")
    return 0


def cmd_evals_list(args: argparse.Namespace) -> int:
    for scenario in load_scenarios(args.root):
        print(f"{scenario.name}: {scenario.goal}")
    return 0


def cmd_evals_report(args: argparse.Namespace) -> int:
    report = build_report(args.root)
    if args.output:
        write_json_utf8(args.output, report)
        print(f"wrote eval report: {args.output}")
    else:
        print_json(report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-win", description="Codex Win11 简中效率工具箱")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("preflight", help="检查 gh / repo / Python 等基础状态")
    p.add_argument("--cwd", default=None)
    p.set_defaults(func=cmd_preflight)

    enc = sub.add_parser("encoding", help="中文文件编码辅助")
    enc_sub = enc.add_subparsers(dest="encoding_command", required=True)
    p = enc_sub.add_parser("check", help="检查文件编码和 mojibake")
    p.add_argument("path")
    p.set_defaults(func=cmd_encoding_check)
    p = enc_sub.add_parser("write-json", help="把 JSON 输入正规化写成 UTF-8 / ensure_ascii=False")
    p.add_argument("path")
    p.add_argument("--input", required=True)
    p.add_argument("--sort-keys", action="store_true")
    p.set_defaults(func=cmd_encoding_write_json)

    prb = sub.add_parser("pr-body", help="PR body 正规化和校验")
    prb_sub = prb.add_subparsers(dest="pr_body_command", required=True)
    p = prb_sub.add_parser("normalize")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_pr_body_normalize)
    p = prb_sub.add_parser("validate")
    p.add_argument("path")
    p.add_argument("--no-required-sections", action="store_true")
    p.set_defaults(func=cmd_pr_body_validate)

    ghp = sub.add_parser("gh", help="Codex 友好的 gh 包装")
    gh_sub = ghp.add_subparsers(dest="gh_command", required=True)
    p = gh_sub.add_parser("preflight")
    p.add_argument("--cwd", default=None)
    p.add_argument("--hostname", default="github.com")
    p.set_defaults(func=cmd_gh_preflight)
    p = gh_sub.add_parser("pr-view")
    p.add_argument("--pr", required=True)
    p.add_argument("--cwd", default=None)
    p.set_defaults(func=cmd_gh_pr_view)
    p = gh_sub.add_parser("pr-create")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--draft", action="store_true")
    p.add_argument("--cwd", default=None)
    p.set_defaults(func=cmd_gh_pr_create)
    p = gh_sub.add_parser("pr-edit")
    p.add_argument("--pr", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--base", default=None)
    p.add_argument("--head", default=None)
    p.add_argument("--draft", choices=["true", "false"], default=None)
    p.add_argument("--cwd", default=None)
    p.set_defaults(func=cmd_gh_pr_edit)
    p = gh_sub.add_parser("pr-verify")
    p.add_argument("--pr", required=True)
    p.add_argument("--title", default=None)
    p.add_argument("--body-file", default=None)
    p.add_argument("--base", default=None)
    p.add_argument("--head", default=None)
    p.add_argument("--draft", choices=["true", "false"], default=None)
    p.add_argument("--cwd", default=None)
    p.set_defaults(func=cmd_gh_pr_verify)

    sh = sub.add_parser("shell", help="Shell 方言检查")
    sh_sub = sh.add_subparsers(dest="shell_command", required=True)
    p = sh_sub.add_parser("lint")
    p.add_argument("--shell", default=None)
    p.add_argument("--command", required=True)
    p.set_defaults(func=cmd_shell_lint)

    ag = sub.add_parser("agents", help="AGENTS.md 检查")
    ag_sub = ag.add_subparsers(dest="agents_command", required=True)
    p = ag_sub.add_parser("lint")
    p.add_argument("path")
    p.add_argument("--max-lines", type=int, default=220)
    p.set_defaults(func=cmd_agents_lint)

    p = sub.add_parser("install-template", help="复制 AGENTS/hooks 模板到目标仓库")
    p.add_argument("--profile", choices=["balanced", "strict"], default="balanced")
    p.add_argument("--target", required=True)
    p.add_argument("--hooks", action="store_true", help="同时复制 .codex/hooks.json")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_install_template)

    ev = sub.add_parser("evals", help="eval 场景元信息")
    ev_sub = ev.add_subparsers(dest="eval_command", required=True)
    p = ev_sub.add_parser("list")
    p.add_argument("--root", default=None)
    p.set_defaults(func=cmd_evals_list)
    p = ev_sub.add_parser("report")
    p.add_argument("--root", default=None)
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_evals_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # CLI should fail with concise, actionable message.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
