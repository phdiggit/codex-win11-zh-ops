# Win11 Shell 路由 Playbook

## 默认策略

- 当前 shell 能可靠执行时保持当前 shell。
- 避免 PowerShell 与 Bash 多层嵌套引号。
- 中文长文本不要 inline 进 shell 命令。
- 需要结构化读写时优先 Python。

## Windows PowerShell 5.1

适合：`.ps1`、注册表、服务、Defender、AppX、Windows 对象管道。

禁止：

```powershell
git status && git diff
# 不要在 PowerShell 5.1 中使用 Bash here-doc
# 不要使用 gh pr create --body "包含中文和多行 Markdown 的正文"
```

推荐：

```powershell
git -c core.quotepath=false status --short
if ($?) { git -c core.quotepath=false diff --name-only }
python scripts/dev/write_body.py
codex-win pr-body validate .tmp/pr-bodies/body.md
gh pr create --body-file .tmp/pr-bodies/body.md --title "..." --base main --head codex/task
```

## PowerShell 7

PowerShell 7 支持更多语法，但在 Codex 自动化里仍建议避免把大段中文 inline 到命令中。跨机器脚本要明确声明需要 `pwsh`。

## Git Bash

适合：简单 `git`、`rg`、文本搜索、Linux 风格小工具。只有路径实际存在且项目允许时使用，不把个人机器固定路径写入项目模板。

## Python

适合：中文 Markdown/JSON/PR body、路径扫描、正文正规化、读回比较。默认显式 UTF-8。
