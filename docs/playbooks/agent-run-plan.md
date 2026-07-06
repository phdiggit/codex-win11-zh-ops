# Agent run-plan 接入 Playbook

`codex-win agent run-plan` 的边界是“Codex CLI 子进程监管和通用产物契约”。它负责后台启动、并发、超时、心跳、Windows 进程树清理、权限画像、输出兜底和结果收集；业务仓库继续负责 workitem 生成、prompt 内容、业务 schema、readiness 和落库。

## 推荐命令

适合 retrieval/patch 类任务的默认接入方式：

```powershell
codex-win agent run-plan `
  --tasks-jsonl tmp\codex_tasks.jsonl `
  --output-root tmp\agent_run `
  --cwd . `
  --permission-profile tmp-jsonl-review `
  --deny-policy deny-rewrite `
  --git-snapshot minimal `
  --max-workers 4 `
  --timeout-seconds 1800
```

后台模式可加 `--background`，再用 `status`、`wait`、`collect`、`kill`、`cleanup-stale` 收尾：

```powershell
codex-win agent status --output-root tmp\agent_run
codex-win agent wait --output-root tmp\agent_run --timeout-seconds 1800
codex-win agent collect --output-root tmp\agent_run
codex-win agent kill --output-root tmp\agent_run
codex-win agent cleanup-stale --output-root tmp\agent_run
```

## 任务 JSONL

每行一个 task。首版建议业务仓库至少写出这些字段：

```json
{"task_code":"MRT-001","prompt_path":"tmp/retrieval_v2/prompts/MRT-001.md","patch_path":"tmp/retrieval_v2/patches/MRT-001.jsonl","last_message_path":"tmp/retrieval_v2/logs/MRT-001.last.md","log_path":"tmp/retrieval_v2/logs/MRT-001.events.jsonl","permission_profile":"tmp-jsonl-review","deny_policy":"deny-rewrite","expected_outputs":[{"kind":"jsonl_patch","path":"tmp/retrieval_v2/patches/MRT-001.jsonl","fallback":"last_message_marked_block","begin":"PATCH_JSONL_BEGIN","end":"PATCH_JSONL_END"}]}
```

字段含义：

- `task_code`：稳定任务 ID，必须避免重复。
- `prompt_path`：UTF-8 prompt 文件；run-plan 会把内容写入子 Codex stdin。
- `patch_path`：兼容旧任务契约的主要 JSONL patch 输出。
- `last_message_path`：子 Codex final message；也是 fallback 恢复来源。
- `log_path`：Codex JSON event log。
- `permission_profile`：任务权限画像，推荐 `tmp-jsonl-review`。
- `deny_policy`：越权或策略拒绝后的处理，推荐 `deny-rewrite`。
- `expected_outputs`：通用输出契约，不包含业务字段判断。

可复制样例在 `examples/agent-run-plan/codex_tasks.jsonl`。

## 权限画像

常用 profile：

- `review-only`：只读任务上下文，只能写 last message，不写 repo。
- `tmp-jsonl-review`：可写 `tmp/**` 下的 patch/log/report，禁止源码修改、DB、网络和 git 写命令。
- `local-write`：允许工作区白名单写入，适合受控生成物。
- `repo-editor`：允许编辑源码，但仍禁止危险 git destructive 命令。
- `bypass`：显式高级模式，只应由主控点名使用。

声明 profile 后，`patch_path`、`expected_output_path`、`expected_outputs[*].path` 必须位于允许写根内；不满足时任务会在启动子 Codex 前失败，错误类型为 `permission_output_path_denied`。

## deny policy

- `deny-fail` / `fail`：发现策略拒绝或越权证据即失败。
- `deny-continue`：产物契约已满足时降级为风险记录，否则失败。
- `deny-rewrite` / `continue-with-final`：只有从 last message fallback 成功恢复产物时才降级，否则失败。

retrieval/patch 类任务推荐 `deny-rewrite`。这样子 Codex 即使因为禁止命令走偏，只要 final message 中按标记输出 JSONL，run-plan 仍可恢复 patch 文件；如果没有可恢复产物，就不会伪装成成功。

## 输出契约

`jsonl_patch` 会检查：

- 文件存在。
- 文件非空。
- 每行是 JSON object。
- 可选 `expected_min_bytes` / `expected_line_count` 达标。

当声明：

```json
{"fallback":"last_message_marked_block","begin":"PATCH_JSONL_BEGIN","end":"PATCH_JSONL_END"}
```

且目标文件缺失时，run-plan 会从 last message 标记块恢复 JSONL 文件。业务脚本只需要消费 `results.jsonl` 和 patch 文件，不需要判断子 Codex 是直接写文件还是 final-message fallback。

## 只读上下文

当 profile 禁止子 agent 自行运行 `git status` 时，supervisor 会预先采集 `readonly_equivalents.git_context_snapshot`，包含：

- `git status --short --branch`
- `git rev-parse HEAD`
- 当前 branch
- repo root

默认 `--git-snapshot minimal` 只注入上述轻量摘要，避免给每个子任务增加固定大段 token。需要完整变更摘要时使用 `--git-snapshot full`，会额外注入：

- `git diff --stat`
- `git diff --name-status`

完全不需要 git 上下文时使用 `--git-snapshot none`。任务 JSON 可用 `git_snapshot` 覆盖单个 task，例如某个任务写 `"git_snapshot":"full"`。这份快照会进入 `results.jsonl` 的 permission record，并被注入 prompt prelude。子 agent 应使用快照，不要自行运行被禁止的 git 命令。

## 输出文件

每个 `output-root` 固定写入：

```text
status.json      当前 run 状态、任务状态、PID、heartbeat、totals
tasks.jsonl      正规化后的任务快照
children.jsonl   supervisor 和 task 子进程事件
results.jsonl    每个 task 的退出码、耗时、usage、prompt/stdin、sandbox、失败摘要和输出路径
summary.json     collect 或最终状态摘要
logs/*           默认 stdout/event log、stderr 和 last message
```

任务成功不只看进程退出码。若 JSON event log 出现 `type=error`、`turn.failed`、usage/rate/auth limit，或产物契约不满足，任务会标为 failed，并在 `results.jsonl` 写入 `error_type`、`error`、`event_analysis`。

## 接入边界

放在 `codex-win`：

- Codex CLI 子进程启动、后台 supervisor、并发和 timeout。
- PID/进程树/heartbeat/stdout/stderr/last message/event log。
- `status.json`、`tasks.jsonl`、`children.jsonl`、`results.jsonl`、`summary.json`。
- 权限画像、写路径预检、deny policy、readonly context、fallback 恢复。
- Windows 下可靠 tree kill 和 stale cleanup。

留在业务仓库：

- workitem 生成。
- prompt 业务内容。
- patch schema 和业务字段校验。
- readiness、dry-run、幂等校验和 `--execute` 落库。
- 业务事实判断，例如人物身份、人才等级、证据来源等。

## 已知限制

首版不是完整命令拦截器。它通过 Codex sandbox、prompt 前置边界、产物契约、日志分析和结果降级来管理风险；如果业务需要强制阻断所有子命令，仍应在业务侧或运行环境侧增加更强隔离。
