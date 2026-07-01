# Win11 简体中文 Codex 全局规则模板

适用范围：Windows 11 + 简体中文用户环境。涉及 PowerShell 的场景一律优先使用 PowerShell 7.x (`pwsh.exe`)；项目仓库仍应有自己的根 `AGENTS.md`。

## Shell

1. 默认环境为 Windows 11 + 简体中文。
2. 涉及 PowerShell 的命令一律调用 `pwsh.exe`；只有 Windows PowerShell 5.1 专属兼容验证或项目明确要求时才用 `powershell.exe`。
3. 在 `pwsh` 中使用 PowerShell 语法；可以使用 `&&`、`||`，但不要使用 Bash here-doc。
4. 中文 Markdown、JSON、PR body、Issue/PR 评论不得通过 PowerShell inline、管道或 here-string 传递；改用 UTF-8 文件、Python `pathlib`、仓库工具或 Git Bash here-doc。
5. 需要 Bash 工具链、POSIX 管道、`.sh` 脚本或 Bash here-doc 时使用 Git Bash；不要用 `pwsh` 硬替 Bash。
6. 若出现中文乱码、JSON 损坏或复杂嵌套引号，优先改为 UTF-8 临时文件或 Python 脚本，不继续调试易碎的 inline 字符串。

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
