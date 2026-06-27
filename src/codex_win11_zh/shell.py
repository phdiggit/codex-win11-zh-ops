from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable

from .pr_body import inline_body_risk


@dataclass(frozen=True)
class ShellIssue:
    code: str
    severity: str
    message: str
    suggestion: str = ""


def detect_shell_name(explicit: str | None = None) -> str:
    if explicit:
        return explicit.lower()
    shell = os.environ.get("ComSpec") or os.environ.get("SHELL") or ""
    exe = os.path.basename(shell).lower()
    if exe in {"powershell.exe", "powershell"}:
        return "powershell5"
    if exe in {"pwsh.exe", "pwsh"}:
        return "pwsh"
    if exe in {"bash.exe", "bash", "sh"}:
        return "bash"
    if sys.platform.startswith("win"):
        return "powershell5"
    return "bash"


def _contains_chain_operator(command: str) -> bool:
    # Good enough for a guardrail: high-confidence detection is preferred over a
    # complex shell parser.
    return "&&" in command or "||" in command


def _contains_bash_here_doc(command: str) -> bool:
    return bool(re.search(r"<<\s*['\"]?[A-Za-z_][A-Za-z0-9_]*['\"]?", command))


def _contains_dangerous_delete(command: str) -> bool:
    patterns = [
        r"\brm\s+-[^\n]*r[^\n]*f\b",
        r"\bgit\s+clean\s+[^\n]*-[^\n]*f",
        r"\bRemove-Item\b[^\n]*(?:-Recurse|-r)\b[^\n]*(?:-Force|-f)\b",
        r"\brmdir\s+/s\b",
        r"\bdel\s+/s\b",
    ]
    return any(re.search(p, command, re.IGNORECASE) for p in patterns)


def lint_command(command: str, *, shell: str | None = None) -> list[ShellIssue]:
    shell_name = detect_shell_name(shell)
    issues: list[ShellIssue] = []

    is_winps = shell_name in {"powershell5", "winps", "windows-powershell", "powershell"}
    if is_winps and _contains_chain_operator(command):
        issues.append(
            ShellIssue(
                code="PS51_CHAIN_OPERATOR",
                severity="error",
                message="Windows PowerShell 5.1 不支持 `&&` 或 `||`。",
                suggestion="拆成多条命令，或使用 PowerShell 原生控制流：`cmd1`; `if ($?) { cmd2 }`。",
            )
        )

    if is_winps and _contains_bash_here_doc(command):
        issues.append(
            ShellIssue(
                code="PS_BASH_HEREDOC",
                severity="error",
                message="PowerShell 不支持 Bash here-doc 语法。",
                suggestion="大段中文或脚本内容请先写入 UTF-8 文件，再调用 Python/gh。",
            )
        )

    if inline_body_risk(command):
        issues.append(
            ShellIssue(
                code="GH_INLINE_BODY_RISK",
                severity="error",
                message="检测到高风险 `gh pr create/edit --body` inline 正文。",
                suggestion="先写 `.tmp/pr-bodies/*.md` UTF-8 文件，再使用 `gh --body-file <file>` 并读回验证。",
            )
        )

    if _contains_dangerous_delete(command):
        issues.append(
            ShellIssue(
                code="DANGEROUS_DELETE",
                severity="warning",
                message="命令包含递归/强制删除或 git clean，可能破坏用户工作区。",
                suggestion="仅在用户明确授权且路径已核对时执行；优先报告并等待确认。",
            )
        )
    return issues


def format_issues(issues: Iterable[ShellIssue]) -> str:
    lines: list[str] = []
    for issue in issues:
        lines.append(f"[{issue.severity.upper()}] {issue.code}: {issue.message}")
        if issue.suggestion:
            lines.append(f"  建议：{issue.suggestion}")
    return "\n".join(lines)
