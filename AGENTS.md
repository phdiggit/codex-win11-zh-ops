# AGENTS.md

本仓库用于研究和沉淀 Codex 在 Windows 11 + 简体中文环境下的效率优化方法。目标是把经验规则、工具脚本、hooks 和 eval 场景做成可复用套件，减少中文编码失败、错误 shell 语法、低效 GitHub 操作和 PR body 写入失败。

## 执行优先级

1. 用户本轮明确要求。
2. 本 `AGENTS.md`。
3. `README.md`、`docs/`、`templates/`、`src/` 中与当前任务直接相关的说明。
4. 其它默认工具习惯。

如规则冲突，优先满足更高层规则。只读分析任务保持只读；实现任务在范围清楚后直接完成必要步骤。

## 任务启动协议

1. 先检查 `git -c core.quotepath=false status --short`，不得覆盖用户已有改动。
2. 按任务路由读取最小必要文件集；不要通读无关大文件。
3. 先盘点现有 CLI、模板、hooks、测试，再做最小改动。
4. 代码改动后先运行定向验证，再运行适用的轻量全量验证。
5. 完成前核对 changed files、未跟踪文件和禁止范围。

## 目录职责

```text
src/codex_win11_zh/     Python 库和 CLI 实现
templates/              可复制到其它项目的 AGENTS、hooks 和工作流模板
docs/                   设计说明、playbook、失败分类
evals/                  可复现实验场景和轻量报告脚本
tests/                  标准库 unittest 测试
```

## Shell、编码与路径

1. 用户可见文档、注释和报告默认使用中文。
2. Python 源码、函数名、参数名、JSON 字段名保持英文。
3. 涉及 PowerShell 的命令一律调用 `pwsh.exe`；只有 Windows PowerShell 5.1 专属兼容验证或项目明确要求时才用 `powershell.exe`。
4. 在 `pwsh` 中使用 PowerShell 语法；可以使用 `&&`、`||`，但不要使用 Bash here-doc。
5. 中文 Markdown、JSON、PR body、评论正文不得通过 PowerShell inline、管道或 here-string 传递；改用 UTF-8 文件、Python `pathlib`、仓库工具或 Git Bash here-doc。
6. 需要 Bash 工具链、POSIX 管道、`.sh` 脚本或 Bash here-doc 时使用 Git Bash；不要用 `pwsh` 硬替 Bash。
7. 若出现中文乱码、JSON 损坏或复杂嵌套引号，优先改为 UTF-8 临时文件或 Python 脚本，不继续调试易碎的 inline 字符串。
8. Git 状态和 diff 文件名核对使用 `git -c core.quotepath=false`。
9. 新增文本文件默认 UTF-8；`.ps1` 如需要兼容 Windows PowerShell 5.1 中文输出，可显式 UTF-8 BOM。

## GitHub 与 PR

1. 当前本地仓库对应 GitHub 远端，且 `gh` 已认证时，默认优先使用 `gh`。
2. GitHub connector 仅在 `gh` 不可用、未认证、权限不足、功能无法完成，或用户明确要求时使用。
3. 不要对同一 Issue、PR、评论重复使用多个接口写入。
4. 中文、多行 Markdown、code fence 或反引号正文必须通过 UTF-8 文件与 `gh --body-file` 写入。
5. 创建或更新 PR 后必须读回 title、body、base、head、Draft 状态并验证正文未损坏。

## 修改方法

1. 小步修改，不顺手重写无关文档、批量格式化全仓库或引入重依赖。
2. CLI 首版优先使用 Python 标准库。
3. hooks 首版只拦截高置信错误，不做复杂平台。
4. eval 场景保持可读、可复制、可手工执行。
5. 模板中的动态事实要少，稳定规则放 AGENTS，当前任务事实放任务卡或 Issue。

## 验证选择

常用验证：

```powershell
python -m compileall src
python -m unittest discover -s tests
codex-win shell lint --shell powershell5 --command "git status && git diff"
codex-win pr-body validate tests/fixtures/pr-body-good.md
```

## 最终报告

完成后说明：

1. 改了哪些主要文件或生成了什么 artifact。
2. 运行了哪些验证命令及结果。
3. 是否存在未完成事项或已知限制。
4. 如生成压缩包，给出下载链接。
