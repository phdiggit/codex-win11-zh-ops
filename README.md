# codex-win11-zh-ops

面向 **Windows 11 + 简体中文 + GitHub 工作流** 的 Codex 效率优化初版项目。

这个仓库把高频失败点拆成三层治理：

1. **AGENTS.md 模板**：告诉 Codex 在 Win11 简中环境中优先怎么做。
2. **Python CLI / 库**：把中文编码、PR body、`gh`、shell 方言检查做成可复用工具。
3. **hooks / evals**：把经验变成可拦截、可复现、可比较的规则。

## 目标问题

- 中文 Markdown / JSON / PR body 读写乱码、反复失败重试。
- 本地已有已认证 `gh` CLI 时仍优先使用 GitHub connector。
- 创建或更新 PR body 时中文、多行 Markdown、code fence 损坏。
- Windows PowerShell 5.1 中错误使用 `&&`、`||`、Bash here-doc 等语法。
- Codex 误改生成文件、越界读取、重复运行重型失败命令。

## 快速开始

```powershell
# 建议在虚拟环境或 pipx 中安装
python -m pip install -e .

# 查看本机 gh / git / shell 预检
codex-win preflight

# 校验 PR body
codex-win body normalize --input draft.md --output .tmp/bodies/body.md
codex-win body validate .tmp/bodies/body.md

# 检查 Windows PowerShell 5.1 命令是否含不兼容语法
codex-win shell lint --shell powershell5 --command "git status && git diff"

# 检查 AGENTS.md 是否包含核心约束
codex-win agents lint AGENTS.md
```

## 项目模板

模板位于：

```text
templates/
  global/      用户全局 AGENTS 模板
  repo/        项目根与子目录 AGENTS、workflow、任务卡和 .gitattributes 模板
  hooks/       Codex hooks 配置模板
```

复制 balanced 模板到目标仓库：

```powershell
codex-win install-template --profile balanced --target C:\path\to\repo
```

严格 profile 更适合中文 PR 多、GitHub 操作频繁、生成文件复杂的仓库：

```powershell
codex-win install-template --profile strict --target C:\path\to\repo
```

`strict` profile 默认会安装 `.codex/hooks.json`；如需显式关闭 hooks，可加 `--no-hooks`。`balanced` profile 默认不安装 hooks，需要时加 `--hooks`。目标目录已有模板文件时，加 `--overwrite` 更新：

```powershell
codex-win install-template --profile strict --target C:\path\to\repo --overwrite
```

路径包含空格时必须加引号；如果当前目录就是目标仓库，也可以用 `--target .`：

```powershell
codex-win install-template --profile strict --target "E:\code\not versioned\testCodex"
codex-win install-template --profile strict --target .
```

维护模板时，根目录 `templates/` 与包内 `src/codex_win11_zh/templates/` 必须保持一致；`python -m unittest discover -s tests` 会检查两份模板是否同步。

## 主要 CLI

```text
codex-win preflight
codex-win encoding check <path>
codex-win encoding write-json <path> --input <json-file>
codex-win body normalize --input <in.md> --output <out.md>
codex-win body validate <body.md>
codex-win pr-body normalize --input <in.md> --output <out.md>
codex-win pr-body validate <body.md>
codex-win gh preflight
codex-win gh pr-view --pr <number-or-url>
codex-win gh pr-create --title ... --body-file ... --base ... --head ...
codex-win gh pr-edit --pr ... --title ... --body-file ... --base ... --head ...
codex-win gh pr-verify --pr ... --title ... --body-file ... --base ... --head ...
codex-win shell lint --shell powershell5 --command "..."
codex-win agents lint AGENTS.md
codex-win evals list
codex-win evals report --output reports/local.json
```

## hooks

项目提供一个轻量 PreToolUse hook：

- 阻止 Windows PowerShell 5.1 的 `&&` / `||`。
- 阻止 PowerShell 场景下的 Bash here-doc。
- 阻止中文或多行 PR body 直接通过 `gh --body "..."` inline 传递。
- 提醒危险命令，例如 `git clean -fd`、`rm -rf`、`Remove-Item -Recurse -Force`。

安装方式：把 `templates/hooks/hooks.json` 复制到目标仓库 `.codex/hooks.json`，并确保目标环境可以运行：

```powershell
python -m codex_win11_zh.hooks.pre_tool_use
```

使用 `codex-win install-template --profile strict --target <repo>` 时会自动复制 hooks。安装后如果 Codex 没有触发 hook，先确认 `.codex/hooks.json` 存在，直接运行 hook 命令确认 Python 可导入，再开启新的 Codex 会话或检查 hooks 配置中的 `matcher` 是否匹配当前工具名。

`codex-win body normalize/validate` 是通用正文入口，适用于 PR body、Issue body、评论正文和 release notes；`codex-win pr-body ...` 保留为兼容别名。

## 验证

```powershell
python -m compileall src
python -m unittest discover -s tests
```

## 初版边界

这是一个 MVP：优先提供可复用结构、核心 CLI 和 guardrail。hooks 和 eval harness 保持轻量，便于后续按真实 Codex 行为继续迭代。
