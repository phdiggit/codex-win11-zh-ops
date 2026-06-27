# Codex 工作流模板

本文件承载根 `AGENTS.md` 不宜展开的详细流程。普通任务先读根规则；涉及基线同步、分级验证、失败处理、提交或 PR 时，再按需读取对应章节。

## 本地任务生命周期

1. 读取根 `AGENTS.md` 与当前 Issue/任务卡。
2. 确认任务定位、禁止范围、预期 changed files 和验收标准。
3. 同步默认分支并从干净工作区创建任务分支。
4. 按任务路由读取最小文件集。
5. 盘点现有规则、调用方、行为和已有模板。
6. 实施最小改动，不顺手扩大范围。
7. 先运行定向验证，再运行适用的最终验证。
8. 核对 changed files、diff、工作区状态和禁止范围。
9. 提交、推送；验证完成后创建 ready PR，验证未完成时创建 Draft 或停止并报告。
10. 输出可核验的最终报告。

## 基线同步

```powershell
git fetch origin
git checkout main
git pull --ff-only origin main
git -c core.quotepath=false status --short
git checkout -b codex/<short-task>
```

## 工作区保护

```powershell
git -c core.quotepath=false status --short
git -c core.quotepath=false diff --name-only
git ls-files --others --exclude-standard
```

如果工作区在任务开始时不为空：不覆盖、不 stash、不清理，停止并报告已有改动文件。

## Shell 和编码

- Windows PowerShell 5.1 中不要用 `&&` 或 `||` 串联命令。
- 中文 PR body、评论正文或其它需要交给 `gh --body-file` 的文本，先写 UTF-8 文件。
- 不通过 PowerShell 管道或命令行字符串直接传中文正文。
- 中文路径和 changed files 使用 `git -c core.quotepath=false`。

## 模板安装后自检

```powershell
codex-win preflight
codex-win agents lint AGENTS.md
codex-win shell lint --shell powershell5 --command "git status && git diff"
python -m json.tool .codex/hooks.json
```

`shell lint` 对 Windows PowerShell 5.1 的 `&&` 返回错误是预期结果。安装 hooks 后，在 Codex 中尝试一条只读命令 `git status && git diff`；如果未被拦截，检查 `.codex/hooks.json` 的 `matcher` 是否匹配当前环境中的工具名。

`strict` profile 默认安装 `.codex/hooks.json`。如果是在已有项目中补装或更新模板，使用：

```powershell
codex-win install-template --profile strict --target . --overwrite
```

路径包含空格时必须加引号，例如 `--target "E:\code\not versioned\testCodex"`；如果当前目录就是目标仓库，优先用 `--target .`。

如果安装后当前 Codex 会话仍未触发 hook，按以下顺序排查：

1. 确认 `.codex/hooks.json` 存在。
2. 直接运行 `python -m codex_win11_zh.hooks.pre_tool_use`，确认 hook 命令可导入。
3. 开启新的 Codex 会话后再验证。
4. 检查 `.codex/hooks.json` 的 `matcher` 是否匹配当前环境中的工具名。

## PR body 推荐流程

```powershell
codex-win body normalize --input draft.md --output .tmp/bodies/body.md
codex-win body validate .tmp/bodies/body.md
codex-win gh pr-create --title "<title>" --body-file .tmp/bodies/body.md --base main --head <branch>
codex-win gh pr-verify --pr <number-or-url> --title "<title>" --body-file .tmp/bodies/body.md --base main --head <branch> --draft false
```

`codex-win body normalize/validate` 适用于 PR body、Issue body、评论正文和 release notes；`codex-win pr-body ...` 是兼容入口。

PR body 至少包含：

1. 摘要。
2. 范围和修改文件。
3. 验证命令和结果。
4. 风险或危险动作说明。
5. 未解决事项。
6. Issue 引用：阶段性 PR 使用 `Refs #<issue>`；完整关闭才使用 `Closes #<issue>`。

## 停止条件

遇到以下情况停止并报告事实：

- 工作区在任务开始时不为空。
- 无法 fast-forward 到目标基线。
- Issue 与任务卡范围冲突。
- 发现会覆盖用户已有改动。
- 需要真实执行危险动作但没有明确授权。
- 无法确认 PR base。
- 验证未完成但任务要求 ready for review。
