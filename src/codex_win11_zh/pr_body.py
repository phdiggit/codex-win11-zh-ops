from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .encoding import detect_mojibake, read_text_auto, write_utf8_no_bom

REQUIRED_SECTION_PATTERNS = {
    "summary": re.compile(r"^#{1,3}\s*(摘要|Summary|Overview).*", re.IGNORECASE | re.MULTILINE),
    "scope": re.compile(r"^#{1,3}\s*(范围|修改文件|Changed files|Scope).*", re.IGNORECASE | re.MULTILINE),
    "validation": re.compile(r"^#{1,3}\s*(验证|Validation|Tests?).*", re.IGNORECASE | re.MULTILINE),
    "risk": re.compile(r"^#{1,3}\s*(风险|危险动作|Risk|Risks).*", re.IGNORECASE | re.MULTILINE),
    "unresolved": re.compile(r"^#{1,3}\s*(未解决|Open items|Unresolved|Follow-up).*", re.IGNORECASE | re.MULTILINE),
    "issue": re.compile(r"\b(Refs|Closes|Fixes|Resolves)\s+#\d+\b|Issue\s*[:：]\s*#\d+", re.IGNORECASE),
}

FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_BODY_RE = re.compile(r"\bgh\s+pr\s+(create|edit)\b.*\s--body\s+(['\"])", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    suggestion: str = ""


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Remove trailing whitespace outside code blocks, preserving intentional code
    # block contents.
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line.rstrip())
        elif in_fence:
            out.append(line)
        else:
            out.append(line.rstrip())
    normalized = "\n".join(out).strip() + "\n"
    return normalized


def normalize_file(input_path: str | Path, output_path: str | Path) -> None:
    result = read_text_auto(input_path)
    normalized = normalize_text(result.text)
    write_utf8_no_bom(output_path, normalized)


def code_fences_balanced(text: str) -> bool:
    in_fence = False
    fence_marker = ""
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if not match:
            continue
        marker = match.group(1)
        if not in_fence:
            in_fence = True
            fence_marker = marker
        elif marker == fence_marker:
            in_fence = False
            fence_marker = ""
    return not in_fence


def validate_text(text: str, *, require_sections: bool = True) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not text.strip():
        issues.append(ValidationIssue("EMPTY_BODY", "PR body 为空。", "补充摘要、范围、验证、风险和 Issue 引用。"))

    for issue in detect_mojibake(text):
        issues.append(ValidationIssue(issue.code, issue.message, issue.suggestion))

    if not code_fences_balanced(text):
        issues.append(
            ValidationIssue(
                "UNBALANCED_CODE_FENCE",
                "Markdown code fence 未成对。",
                "检查 ``` 或 ~~~ 是否遗漏闭合。",
            )
        )

    if require_sections:
        for name, pattern in REQUIRED_SECTION_PATTERNS.items():
            if not pattern.search(text):
                issues.append(
                    ValidationIssue(
                        f"MISSING_SECTION_{name.upper()}",
                        f"PR body 缺少推荐字段：{name}",
                        "建议包含摘要、范围和修改文件、验证、风险或危险动作、未解决事项、Issue 引用。",
                    )
                )
    return issues


def validate_file(path: str | Path, *, require_sections: bool = True) -> list[ValidationIssue]:
    result = read_text_auto(path)
    issues = validate_text(result.text, require_sections=require_sections)
    if result.encoding == "cp936":
        issues.append(
            ValidationIssue(
                "LEGACY_CP936",
                "PR body 文件不是 UTF-8。",
                "先用 normalize 写成 UTF-8 文件，再传给 gh --body-file。",
            )
        )
    return issues


def compare_body(local_text: str, remote_text: str) -> list[ValidationIssue]:
    local = normalize_text(local_text).rstrip("\n")
    remote = normalize_text(remote_text).rstrip("\n")
    if local != remote:
        return [
            ValidationIssue(
                "REMOTE_BODY_MISMATCH",
                "远端 PR body 与本地 body 文件不一致。",
                "用 gh pr view --json body 读回正文，检查编码、换行、裁剪或 shell 转义问题。",
            )
        ]
    return []


def inline_body_risk(command: str) -> bool:
    if not INLINE_BODY_RE.search(command):
        return False
    # Inline body is risky for Chinese, multiline Markdown, code fences, and long text.
    return any("\u4e00" <= ch <= "\u9fff" for ch in command) or "```" in command or len(command) > 240
