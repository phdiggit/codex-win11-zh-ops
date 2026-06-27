# 摘要

修复中文 PR body 写入流程，避免编码损坏。

# 范围和修改文件

- `src/codex_win11_zh/pr_body.py`
- `tests/test_pr_body.py`

# 验证

```powershell
python -m unittest discover -s tests
```

# 风险

未执行危险操作。

# 未解决事项

无。

Refs #123
