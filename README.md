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

# 用 UTF-8 环境运行 Python / pytest / validator / export / build 命令
codex-win run -- python -m compileall src

# 规划 focused tests 与 full pytest 预算
codex-win test plan --base origin/main --head HEAD --format text

# dry-run 检查生成物清理范围
codex-win cleanup generated --profile markdown-exports --target .

# 生成 PR review package 的机械事实层
codex-win review-pack --pr 3 --base main --output .tmp/review-pack.md

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
codex-win run -- <command...>
codex-win run --log .tmp/codex-commands.jsonl -- <command...>
codex-win agent run-plan --tasks-jsonl tmp/codex_tasks.jsonl --output-root tmp/agent_run --max-workers 4 --background
codex-win agent status --output-root tmp/agent_run
codex-win agent wait --output-root tmp/agent_run
codex-win agent kill --output-root tmp/agent_run
codex-win agent collect --output-root tmp/agent_run
codex-win timer start --id <task-id> --state .tmp/codex-timer.json
codex-win timer mark --id <task-id> --label <phase> --state .tmp/codex-timer.json
codex-win timer finish --id <task-id> --state .tmp/codex-timer.json --command-log .tmp/codex-commands.jsonl --output .tmp/codex-timing.json
codex-win encoding check <path>
codex-win encoding write-json <path> --input <json-file>
codex-win body normalize --input <in.md> --output <out.md>
codex-win body validate <body.md>
codex-win body apply --pr <number-or-url> --body-file <body.md>
codex-win pr-body normalize --input <in.md> --output <out.md>
codex-win pr-body validate <body.md>
codex-win gh preflight
codex-win gh pr-view --pr <number-or-url>
codex-win gh pr-create --title ... --body-file ... --base ... --head ...
codex-win gh pr-edit --pr ... --title ... --body-file ... --base ... --head ...
codex-win gh pr-verify --pr ... --title ... --body-file ... --base ... --head ...
codex-win shell lint --shell powershell5 --command "..."
codex-win cleanup generated --profile markdown-exports --target <repo> [--apply]
codex-win test plan --base origin/main --head HEAD --changed-files changed.txt
codex-win review-pack --pr <number-or-url> --base <branch> --output .tmp/review-pack.md
codex-win review-pack apply --pr <number-or-url> --package-file .tmp/review-pack.md --body-file .tmp/pr-body.md
codex-win agents lint AGENTS.md
codex-win evals list
codex-win evals report --output reports/local.json
```

## 运行包装器

`codex-win run -- <command...>` 只做一件事：在子进程环境中设置 `PYTHONUTF8=1` 和 `PYTHONIOENCODING=utf-8`，然后原样运行命令并返回原始退出码。它不假设项目结构，也不替代 shell lint；适合 Windows 上的 Python、pytest、validator、export、build 等容易受到 cp936/GBK 影响的命令。

需要记录命令耗时时，加 `--log` 追加 UTF-8 / LF JSONL 记录；工具仍返回原始退出码：

```powershell
codex-win run --log .tmp/codex-commands.jsonl --summary "focused validation" -- python -m unittest discover -s tests
```

每条记录包含 `started_at`、`finished_at`、`duration_sec`、`command`、`exit_code`、`result` 和可选 `summary`。记录只表示被包装命令的 wall time，不代表 Codex 思考、审查或人工等待总耗时。

## Agent 运行监管

`codex-win agent run-plan` 从现有 `codex_tasks.jsonl` 读取任务，负责 Codex CLI 子进程的后台启动、并发、超时、心跳、stdout/stderr、last message、结果和 Windows 进程树清理。它只理解机械运行契约，不理解项目业务 schema；例如 retrieval_v2 的 patch 字段、人才等级、身份归属和落库 readiness 仍由业务仓库校验。

```powershell
codex-win agent run-plan `
  --tasks-jsonl tmp\profile_basis\codex_tasks.jsonl `
  --output-root tmp\profile_basis\agent_run `
  --cwd E:\code\git\my-cloud\github\emperor-evaluation `
  --background `
  --max-workers 4 `
  --timeout-seconds 1800 `
  --sandbox-profile read-only
```

`codex_tasks.jsonl` 第一版兼容常见字段：`task_code`、`prompt_path`、`patch_path`、`last_message_path`、`log_path`、`argv`。也可以声明通用产物契约：`expected_output_path`、`expected_min_bytes`、`expected_line_count`。默认不会原样执行任务里的 `argv`，而是由 `codex-win` 重新组装 read-only Codex 命令；显式加 `--respect-task-argv` 时会保留任务 argv，但若识别到 `codex exec` 且缺少 stdin/JSON/last-message 参数，会补上 `-`、`--json`、`--output-last-message`，避免 prompt 文件没有进入子 Codex。两种模式都会把 `prompt_path` 的 UTF-8 内容写入 Codex stdin，并在结果里记录 prompt 字节数和 stdin 写入状态。

需要写工作区时用 `--sandbox-profile local-write`，危险 bypass 必须显式写 `--sandbox-profile bypass`。`local-write` 会使用 Codex 的 `workspace-write`，并把 `<cwd>\tmp` 作为额外可写目录传给子 Codex，适合把 patch/result 产物落在仓库临时目录下。不使用 `--respect-task-argv` 时，`sandbox-profile` 会覆盖任务原 `argv` 中的 sandbox 选择；`results.jsonl` 的 `command_info` 会记录原 argv sandbox、实际 sandbox、是否发生覆盖，以及额外可写目录。

也可以用更高层的权限画像声明子 agent 的任务边界：

```powershell
codex-win agent run-plan `
  --tasks-jsonl tmp\profile_basis\codex_tasks.jsonl `
  --output-root tmp\profile_basis\agent_run `
  --permission-profile tmp-jsonl-review `
  --deny-policy continue-with-final `
  --write-root tmp\profile_basis
```

首版内置 `review-only`、`tmp-jsonl-review`、`local-write`、`repo-editor`、`bypass`。`permission_profile` 会写入任务快照和 `results.jsonl`，并映射到有效 sandbox、额外可写目录、允许/禁止命令提示、能力位和 prompt 前置权限边界。即使使用 `--respect-task-argv`，声明了 profile 的任务也会按 profile 约束 `codex exec` 的 sandbox；例如 `tmp-jsonl-review` 不会允许原 argv 里的 bypass 继续生效。任务 JSON 可以覆盖全局值：`permission_profile`、`deny_policy`、`allowed_write_paths`、`allowed_commands`、`denied_commands`。这不是完整命令拦截器；首版通过 Codex sandbox、prompt 边界、产物契约和结果分析兜住长任务风险。

任务可以声明通用输出契约：

```json
{
  "task_code": "MRT-001",
  "permission_profile": "tmp-jsonl-review",
  "expected_outputs": [
    {
      "kind": "jsonl_patch",
      "path": "tmp/retrieval_v2/patches/MRT-001.jsonl",
      "fallback": "last_message_marked_block",
      "begin": "PATCH_JSONL_BEGIN",
      "end": "PATCH_JSONL_END"
    }
  ]
}
```

`jsonl_patch` 会检查文件存在、非空和 JSONL object 行格式。若声明 `fallback: last_message_marked_block` 且文件未写出，run-plan 会从 last message 的 `begin/end` 标记块恢复 JSONL 文件。`deny-policy=continue-with-final` 作为兼容别名等价于 `deny-rewrite`。

声明了 profile 时，`patch_path`、`expected_output_path` 和 `expected_outputs[*].path` 必须位于允许写根内；否则任务会在启动子 Codex 前以 `permission_output_path_denied` 失败，避免长任务跑完才发现产物越界。

`permission_analysis` 会把可观测的命令风险写成结构化事件：命中 `denied_commands`、git 写命令、被禁数据库命令、被禁网络命令等。策略处理结果写入 `deny_resolution`：

- `deny-fail` / `fail`：发现策略拒绝或越权证据即失败。
- `deny-continue`：产物契约已满足时降级为风险记录，否则失败。
- `deny-rewrite` / `continue-with-final`：只有从 last message fallback 成功恢复产物时才降级；否则失败。

每个 `output-root` 会写入这些通用文件：

```text
status.json      当前 run 状态、任务状态、PID、heartbeat、totals
tasks.jsonl      正规化后的任务快照
children.jsonl   supervisor 和 task 子进程事件
results.jsonl    每个 task 的退出码、耗时、timeout、usage、prompt/stdin、sandbox、失败摘要和输出路径
summary.json     collect 或最终状态摘要
logs/*           默认 task stdout/event log、stderr 和 last message；若任务指定 log_path/last_message_path 则写到任务指定位置
```

常用收尾命令：

```powershell
codex-win agent status --output-root tmp\profile_basis\agent_run
codex-win agent wait --output-root tmp\profile_basis\agent_run --timeout-seconds 1800
codex-win agent collect --output-root tmp\profile_basis\agent_run
codex-win agent kill --output-root tmp\profile_basis\agent_run
codex-win agent cleanup-stale --output-root tmp\profile_basis\agent_run
```

任务成功不只看进程退出码。若 Codex JSON event log 出现 `type=error`、`turn.failed`、usage limit、rate limit、auth error，或 stderr/last message 暗示策略拒绝，任务会标为 `failed`，并在 `results.jsonl` 写入 `error_type`、`error`、`event_analysis`。如果任务声明了 `patch_path`、`expected_output_path` 或最小大小/行数要求，run-plan 会做通用文件存在性检查；不满足时不会标 `succeeded`。任务可显式声明 `patch_fallback_from_last_message: true`，允许在 `patch_path` 缺失时从 last message 中的 fenced `jsonl`/`json` 块或 JSON 数组恢复为 JSONL patch。

`collect` 会检查重复 `task_code`、重复结果、last message 是否存在/为空，以及 patch/event JSONL 是否可解析；它不判断 JSONL payload 的业务含义，也不 apply patch、不写数据库。

dry-run 产生的 `planned` 任务不会做 last message、patch 或 event log 输出检查，避免把未执行任务和历史日志误判为失败。`kill` 和 `cleanup-stale` 会在 `status.json` / `children.jsonl` 中记录 `target_pids` 与 `killed_pids`，用于核对本次清理尝试覆盖了哪些 supervisor/task 进程。

`status.json` 使用唯一临时文件和 Windows `os.replace` retry 写入；`results.jsonl` / `children.jsonl` 追加写入也会在同一 supervisor 进程内加锁并重试，避免多 worker 互相踩写。

## 任务计时

跨多条命令的任务 wall time 可用轻量 timer 记录：

```powershell
codex-win timer start --id task-review --state .tmp/codex-timer.json
codex-win timer mark --id task-review --label focused_validation --state .tmp/codex-timer.json
codex-win timer finish --id task-review --state .tmp/codex-timer.json --command-log .tmp/codex-commands.jsonl --output .tmp/codex-timing.json
```

`timer finish` 输出会分开写明 measured task wall time、measured command time、unmeasured time 和 qualitative notes。未单独测量的人工/推理时间保持 `unknown`，工具不会用 wall time 减 command time 推断“人工耗时”。PR body 中的 timing 必须来自这些测量记录，或明确写 `precise timing unavailable`；不要凭感觉写精确分钟数。

## 生成物清理

`codex-win cleanup generated` 默认 dry-run，只有加 `--apply` 才删除。内置 `markdown-exports` profile 覆盖常见的 `exports/markdown_views/**`，并默认排除 `**/.gitkeep`，避免删除保留空目录的占位文件。项目可通过 JSON 配置扩展或覆盖：

```json
{
  "profiles": {
    "project-generated": {
      "extends": "markdown-exports",
      "patterns": ["reports/generated/**"],
      "exclude": ["reports/generated/keep.md"]
    }
  }
}
```

使用方式：

```powershell
codex-win cleanup generated --profile project-generated --config cleanup.json --target .
codex-win cleanup generated --profile project-generated --config cleanup.json --target . --apply
```

清理只会命中显式 profile 配置的相对路径，路径包含绝对路径或 `..` 会被拒绝。

## 测试预算

`codex-win test plan` 根据 changed files 判断是否需要 full pytest，并尽量推荐 focused tests。默认输出人类摘要和 JSON；脚本场景可用 `--format json`。

```powershell
git -c core.quotepath=false diff --name-only origin/main...HEAD > .tmp/changed-files.txt
codex-win test plan --base origin/main --head HEAD --changed-files .tmp/changed-files.txt --format both
```

策略是：同一 head SHA 的 current-head full pytest 最多记录一次；只有 current-head full pytest 失败、且需要判断基线是否已坏时，才允许 base full pytest。可用 `--record-current-full passed|failed` 或 `--record-base-full passed|failed` 写入轻量状态文件，默认位置是 `.tmp/codex-test-plan-state.json`，落在通常已忽略的临时目录中。

## PR Review Package

`codex-win review-pack` 生成 Codex PR Review Package 的机械事实层，包括 HEAD snapshot、changed files、scope profile 分类、PR body 协议检查、命令日志摘要和人工 findings 占位。它不做 merge 决策，不推断业务语义，也不判断项目特定验收标准或产物正确性。

基本用法：

```powershell
codex-win review-pack --pr <pr> --base main --scope-profile docs --output .tmp/review-pack.md
```

生成包顶部包含 `## Reviewer Quick Summary`，用事实字段提示 `head_status_at_generation`、`head_status_after_apply`、`scope_verdict`、`validation_summary`、`pr_induced_failures`、`fixed_baseline_failures`，并固定写明 `merge_judgment: not_provided_by_tool`。未提供 `--scope-profile` 时，`scope_verdict` 会输出 `unclassified`；这只表示工具没有做 ownership judgment，不表示业务范围已经通过审查。

项目可在 `.codex/review-pack.json` 中定义 scope profile：

```json
{
  "scope_profiles": {
    "docs": {
      "allow": ["docs/**", "README.md", "tests/**"],
      "suspicious": ["src/**", "templates/**"],
      "forbid": [".github/workflows/**"]
    }
  }
}
```

如果有人工记录的命令日志，可通过 `--command-log commands.json` 放入 `## Commands Run`；未提供时只输出当前进程事实，并把验证结论留给 reviewer。命令日志可区分 current/base/historical，也可以直接使用 `codex-win run --log` 产生的 JSONL，或 `timer finish --output` 产生的 timing JSON：

```json
{
  "commands": [
    {
      "command": "codex-win run -- python -m pytest -q tests/test_x.py",
      "result": "passed",
      "summary": "32 passed",
      "kind": "current_focused"
    }
  ],
  "validation": {
    "current_snapshot": [],
    "base_snapshot": [],
    "historical": []
  }
}
```

review package 会额外渲染 `## Timing`：

```text
## Timing
- measured_task_wall_time: `unavailable`
- measured_command_time: `unavailable`
- unmeasured_time: `unknown`
- timing_confidence: `unavailable`
```

只有 command log 或 timer output 中存在测量数据时，字段才会变成具体秒数；未测量时不会生成细分耗时估算。

写回 PR body 时，先生成 review package，再把 package splice 到现有正文中，最后通过 `gh --body-file` 写回并读回验证：

```powershell
codex-win review-pack apply --pr <pr> --package-file .tmp/review-pack.md --body-file .tmp/pr-body.md --command-log commands.json
```

`review-pack apply` 写回前会把 `head_status_after_apply` 更新为 `current`。如果提供 `--command-log`，它会用同一份日志重写 `## Commands Run` 的人工摘要和 JSON block，并同步 quick summary；如果没有日志，但 package 中已有人工填写的验证摘要行，apply 也会把这些 metadata 同步进同一 JSON block，避免同一节出现两套验证事实。

只需要稳定写回完整 PR body 时，可以直接使用通用入口：

```powershell
codex-win body validate .tmp/pr-body.md
codex-win body apply --pr <pr> --body-file .tmp/pr-body.md
```

`review-pack apply` 会替换已有 `# Codex PR Review Package` section；正文侧也兼容旧的 `## Codex PR Review Package v1.1` section，避免追加第二份 review package。写回时会保留 PR body 其它内容，并验证远端正文包含当前 head SHA 和 package marker。生成的 package 默认包含 `## Required Next Actions`，提醒 reviewer 手动审查项目特定 findings，并等待远端检查，除非这些检查已经由 command log 明确提供。

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
