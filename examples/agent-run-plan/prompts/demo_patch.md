# demo_patch

你是一个受 `codex-win agent run-plan` 托管的子 Codex 任务。

请生成一个 JSONL patch，目标路径：

```text
tmp/agent_demo/patches/demo_patch.jsonl
```

每行必须是 JSON object。示例 payload：

```jsonl
{"task_code":"demo_patch","op":"demo","message":"hello from codex-win agent"}
```

如果无法直接写入目标文件，请在最终回复中输出以下标记块，run-plan 会尝试从 last message 恢复：

```text
PATCH_JSONL_BEGIN
{"task_code":"demo_patch","op":"demo","message":"hello from fallback"}
PATCH_JSONL_END
```

不要运行 git、数据库或网络命令；使用 prompt 中给出的上下文完成任务。
