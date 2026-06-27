from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .encoding import read_text_auto, detect_mojibake

RECOMMENDED_KEYWORDS = {
    "shell": ["PowerShell", "&&", "||", "Shell"],
    "encoding": ["UTF-8", "中文", "编码"],
    "github": ["gh", "GitHub", "PR"],
    "workspace": ["git status", "工作区", "changed files"],
    "validation": ["验证", "test", "compileall", "unittest", "pytest"],
}

DYNAMIC_FACT_PATTERNS = [
    re.compile(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b"),
    re.compile(r"当前\s*(Issue|PR|分支|commit|SHA)", re.IGNORECASE),
]


@dataclass(frozen=True)
class AgentsIssue:
    code: str
    severity: str
    message: str
    suggestion: str = ""


def lint_agents_file(path: str | Path, *, max_lines: int = 220) -> list[AgentsIssue]:
    result = read_text_auto(path)
    text = result.text
    issues: list[AgentsIssue] = []

    for enc_issue in detect_mojibake(text):
        issues.append(AgentsIssue(enc_issue.code, "error", enc_issue.message, enc_issue.suggestion))

    if result.encoding == "cp936":
        issues.append(AgentsIssue("LEGACY_CP936", "warning", "AGENTS.md 不是 UTF-8。", "重写为 UTF-8。"))

    line_count = len(text.splitlines())
    if line_count > max_lines:
        issues.append(
            AgentsIssue(
                "AGENTS_TOO_LONG",
                "warning",
                f"AGENTS.md 共有 {line_count} 行，可能过长。",
                "根 AGENTS 保留稳定规则，细节下沉到 docs/ 或子目录 AGENTS。",
            )
        )

    for area, keywords in RECOMMENDED_KEYWORDS.items():
        if not any(k in text for k in keywords):
            issues.append(
                AgentsIssue(
                    f"MISSING_{area.upper()}_POLICY",
                    "warning",
                    f"AGENTS.md 缺少 {area} 相关约束。",
                    "补充 Shell/编码/GitHub/工作区/验证等高频失败点规则。",
                )
            )

    for pattern in DYNAMIC_FACT_PATTERNS:
        if pattern.search(text):
            issues.append(
                AgentsIssue(
                    "POSSIBLE_DYNAMIC_FACT",
                    "info",
                    "AGENTS.md 可能包含会过期的动态事实。",
                    "把当前任务、当前 Issue、当前分支等动态信息放到任务卡或 Issue，不长期写入根规则。",
                )
            )
            break

    return issues
