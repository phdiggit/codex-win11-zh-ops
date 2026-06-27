# Validation

本初版 artifact 生成后已执行以下验证：

```powershell
python -m compileall -q src
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m codex_win11_zh.cli pr-body validate tests/fixtures/pr-body-good.md
PYTHONPATH=src python -m codex_win11_zh.cli evals report --output /tmp/codex_eval_report.json
PYTHONPATH=src python -m codex_win11_zh.cli shell lint --shell powershell5 --command "git status && git diff"
```

结果：

- `compileall` 通过。
- `unittest` 13 个测试全部通过。
- PR body fixture 校验通过。
- eval report 可生成，包含 7 个场景。
- shell lint 对 PowerShell 5.1 的 `&&` 正确返回错误，用于 guardrail。
- 包安装 smoke test 通过，package data 中包含模板文件。
