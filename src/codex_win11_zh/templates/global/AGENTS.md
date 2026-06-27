# Win11 简体中文 Codex 全局规则模板

适用范围：Windows 11 + 简体中文用户环境。建议放在 `~/.codex/AGENTS.md` 或全局 override 中；项目仓库仍应有自己的根 `AGENTS.md`。

## Shell

1. 默认环境为 Windows 11 + 简体中文。
2. Windows PowerShell 5.1 不使用 `&&`、`||` 或 Bash here-doc。
3. 连续步骤拆成多条命令，或使用 PowerShell 原生控制流：`if ($?) { ... }`。
4. 中文 Markdown、JSON、PR body、Issue/PR 评论不得通过 PowerShell inline、管道或 here-string 传递。
5. 需要中文文件读写时优先使用 Python `pathlib`，显式指定 UTF-8。
6. 当前 shell 能可靠执行时保持当前 shell，避免 PowerShell 与 Bash 多层嵌套引号。

## GitHub

1. 当前仓库本地存在且 `gh` 已认证时，GitHub 远端读写优先使用 `gh`。
2. GitHub connector 仅在 `gh` 不可用、未认证、权限不足、功能无法完成，或用户明确要求时使用。
3. 不要对同一 Issue、PR、评论重复使用多个接口写入。
4. fallback 到 connector 时，在最终说明中写清楚原因。

## PR body 与评论正文

1. 中文、多行 Markdown、code fence、反引号、长 changed files 列表必须先写 UTF-8 文件。
2. 使用 `gh pr create/edit --body-file <file>`，不要使用 `--body "..."`。
3. 写入后用 `gh pr view --json title,body,baseRefName,headRefName,headRefOid,isDraft` 读回验证。
4. PR body 至少包含摘要、范围和修改文件、验证、风险或危险动作、未解决事项、Issue 引用。

## Git 与工作区

1. 修改前先运行 `git -c core.quotepath=false status --short`。
2. 不覆盖用户已有改动，不默认 stash、clean、reset。
3. 中文路径、changed files、diff 范围核对使用 `git -c core.quotepath=false`。
4. 提交前检查未提交和未跟踪文件；提交后核对相对 base 的 changed files。
