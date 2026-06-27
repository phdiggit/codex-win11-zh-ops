# 中文编码 Playbook

## 文件策略

- Markdown、JSON、YAML、TXT：UTF-8 no BOM。
- PowerShell 5.1 需要可靠读取中文注释或输出时：`.ps1` 可使用 UTF-8 BOM。
- JSON 写入：`ensure_ascii=False`。

## 高风险模式

- 通过 PowerShell inline 传中文给 Python 或 gh。
- 使用 `gh pr create --body "中文多行正文"`。
- 把 `gh --body-file -` 与管道组合传中文。
- 未读回验证就假设 PR body 写入成功。

## 推荐模式

改用 Python 文件或 CLI：

```powershell
codex-win pr-body normalize --input draft.md --output .tmp/pr-bodies/body.md
codex-win pr-body validate .tmp/pr-bodies/body.md
```

## 检测

```powershell
codex-win encoding check docs/中文文档.md
codex-win pr-body validate .tmp/pr-bodies/body.md
```

发现 U+FFFD 或 mojibake 时，回到原始事实源重读，不要在损坏文本上继续提交。
