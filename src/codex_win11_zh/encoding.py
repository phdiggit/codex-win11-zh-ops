from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

UTF8_BOM = b"\xef\xbb\xbf"
REPLACEMENT_CHAR = "\ufffd"
MOJIBAKE_MARKERS = (
    "Ã",
    "Â",
    "â€",
    "â€™",
    "â€œ",
    "â€�",
    "ä¸",
    "å",
    "æ",
    "ç",
    "ï¼",
    "ï»¿",
)


@dataclass(frozen=True)
class TextReadResult:
    path: Path
    text: str
    encoding: str
    had_bom: bool


@dataclass(frozen=True)
class EncodingIssue:
    code: str
    message: str
    suggestion: str = ""


def decode_bytes(data: bytes) -> tuple[str, str, bool]:
    """Decode bytes with a conservative Windows/UTF-8 strategy.

    UTF-8 is always preferred. CP936 is used only as a fallback so the caller can
    inspect legacy files without silently corrupting them.
    """
    if data.startswith(UTF8_BOM):
        return data.decode("utf-8-sig"), "utf-8-sig", True
    try:
        return data.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        # Useful for legacy Simplified Chinese files. Callers can decide whether
        # to rewrite as UTF-8.
        return data.decode("cp936"), "cp936", False


def read_text_auto(path: str | Path) -> TextReadResult:
    p = Path(path)
    text, encoding, had_bom = decode_bytes(p.read_bytes())
    return TextReadResult(path=p, text=text, encoding=encoding, had_bom=had_bom)


def write_utf8_no_bom(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def write_utf8_bom(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8-sig", newline="\n")


def write_json_utf8(path: str | Path, data: Any, *, indent: int = 2, sort_keys: bool = False) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
    write_utf8_no_bom(path, text + "\n")


def detect_mojibake(text: str) -> list[EncodingIssue]:
    issues: list[EncodingIssue] = []
    if REPLACEMENT_CHAR in text:
        issues.append(
            EncodingIssue(
                code="REPLACEMENT_CHAR",
                message="文本包含 U+FFFD replacement character，通常表示之前发生过解码损坏。",
                suggestion="回到原始文件或远端正文重新读取，不要在已损坏文本上继续提交。",
            )
        )

    marker_hits = [marker for marker in MOJIBAKE_MARKERS if marker in text]
    if marker_hits:
        preview = ", ".join(marker_hits[:6])
        issues.append(
            EncodingIssue(
                code="POSSIBLE_MOJIBAKE",
                message=f"文本包含疑似 mojibake 标记：{preview}",
                suggestion="检查是否把 UTF-8 文本按 ANSI/CP936/Latin-1 错误解码。",
            )
        )
    return issues


def assert_clean_text(text: str) -> None:
    issues = detect_mojibake(text)
    if issues:
        details = "; ".join(f"{i.code}: {i.message}" for i in issues)
        raise ValueError(details)


def roundtrip_check(path: str | Path) -> list[EncodingIssue]:
    result = read_text_auto(path)
    issues = detect_mojibake(result.text)
    if result.encoding == "cp936":
        issues.append(
            EncodingIssue(
                code="LEGACY_CP936",
                message="文件不是 UTF-8，当前通过 CP936 fallback 才能读取。",
                suggestion="确认内容无误后重写为 UTF-8。",
            )
        )
    return issues
