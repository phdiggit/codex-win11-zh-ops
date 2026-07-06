# agent run-plan 示例

这个目录演示一个最小 `codex_tasks.jsonl`：子 Codex 读取 prompt，通过 `tmp-jsonl-review` 权限画像生成 JSONL patch，并在无法直接写文件时从 last message 标记块恢复。

在仓库根目录试跑时，可先把示例复制到临时目录，再按自己的 `codex` 可用性选择真实 Codex 或 fake Codex：

```powershell
codex-win agent run-plan `
  --tasks-jsonl examples\agent-run-plan\codex_tasks.jsonl `
  --output-root tmp\agent_demo\run `
  --permission-profile tmp-jsonl-review `
  --deny-policy deny-rewrite `
  --max-workers 1
```

示例 task 声明了：

- `prompt_path`：`examples/agent-run-plan/prompts/demo_patch.md`
- `patch_path`：`tmp/agent_demo/patches/demo_patch.jsonl`
- `expected_outputs`：`jsonl_patch`，并允许 `PATCH_JSONL_BEGIN` / `PATCH_JSONL_END` fallback。

真实业务仓库应继续在自己的脚本里生成 workitems、prompt 和业务 schema；这里的 JSONL 只展示 codex-win 的通用运行契约。
