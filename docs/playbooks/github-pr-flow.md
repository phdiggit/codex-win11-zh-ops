# GitHub PR Flow Playbook

## 原则

当前本地仓库对应 GitHub 远端，且 `gh` 已认证时，优先使用 `gh`。connector 仅作为 fallback，并在最终报告中说明原因。

## 预检

```powershell
codex-win gh preflight
gh auth status
gh repo view --json nameWithOwner,defaultBranchRef
```

## PR body

正文生成：

```powershell
codex-win pr-body normalize --input draft.md --output .tmp/pr-bodies/body.md
codex-win pr-body validate .tmp/pr-bodies/body.md
```

创建 PR：

```powershell
codex-win gh pr-create --title "<title>" --body-file .tmp/pr-bodies/body.md --base main --head codex/task
```

读回验证：

```powershell
codex-win gh pr-verify --pr <number-or-url> --title "<title>" --body-file .tmp/pr-bodies/body.md --base main --head codex/task --draft false
```

## 失败分类

- `gh` 不存在：可 fallback connector 或提示安装。
- `gh auth status` 失败：不要反复重试正文写入，先处理认证。
- PR body validate 失败：修正文档，不调用远端写入。
- 读回 body mismatch：停止，报告本地 body 路径和远端 PR，不继续覆盖。
