# AGENTS.md

本仓库采用 Codex Win11 简体中文优化规则。目标是在 Windows 11 + 简体中文 + GitHub 工作流下，减少中文编码损坏、无效重试、错误 shell 语法和 PR body 写入失败。

## 执行优先级

1. 用户本轮明确要求。
2. 本 `AGENTS.md`。
3. `README.md`、`docs/`、`scripts/`、`tests/` 中与当前任务直接相关的项目约定。
4. 其它默认工具习惯。

如果规则冲突，优先满足更高层规则。任务卡或 Issue 已给出的分支名、范围、验收标准和禁止事项，不要重复询问。

## 任务启动协议

1. 读取根 `AGENTS.md` 和当前 Issue 或任务卡。
2. 检查默认分支、工作区状态、任务范围、禁止范围和验收标准。
3. 按任务路由读取最小必要文件集；发现新依赖时再增量读取。
4. 先盘点调用方、现有行为和相关约定，再实施最小改动。
5. 先运行定向验证，再运行适用的轻量全量验证。
6. 核对 changed files、禁止范围和工作区状态。
7. 需要 PR 时，提交、推送；验证完成后创建 ready PR，验证未完成时创建 Draft 或停止并报告。
8. 最终报告只写事实、修改摘要、命令结果和未解决问题，不输出私有思维过程。

## 最小读取路由

| 任务类型 | 优先读取 |
|---|---|
| 文档任务 | 根 `AGENTS.md`、任务卡、目标文档、相关索引 |
| 脚本任务 | 根 `AGENTS.md`、任务卡、目标脚本、直接调用方、相关测试 |
| 测试/CI | 根 `AGENTS.md`、任务卡、目标测试、CI 配置、被测试的最小实现集合 |
| GitHub/PR | 根 `AGENTS.md`、任务卡或 Issue/PR、changed files；不默认通读业务文档 |

上下文纪律：

1. 已读取且未变化的文件不重复读取。
2. 优先用 `rg`、路径过滤、函数定位、章节定位和行区间。
3. 不默认打印完整大文件、完整日志、完整 diff 或完整 Issue。
4. 动态事实从事实源读取，不长期写入根规则。

## Shell、编码与路径

1. 用户可见文档、注释和报告默认使用中文。
2. schema 字段名、manifest 键名、函数名、参数名和路径变量名保持英文。
3. 中文 Markdown、JSON、PR body、评论正文不得通过 PowerShell inline、管道或 here-string 传递。
4. 中文长文本先写 UTF-8 文件，再交给目标程序。
5. Windows PowerShell 5.1 不使用 `&&`、`||` 或 Bash here-doc。
6. 连续步骤拆成单条命令，或使用 PowerShell 原生控制流。
7. Git 状态和 diff 核对使用 `git -c core.quotepath=false`。
8. `.ps1` 文件如需要兼容 Windows PowerShell 5.1 中文输出，可使用 UTF-8 BOM。
9. 自动化目录尽量使用英文、数字、短横线，避免 WinPE、cmd、SMB 和日志编码问题。

## GitHub、Commit 与 PR

1. 当前仓库本地存在且 `gh` 已认证时，GitHub 远端读写默认优先使用 `gh`。
2. GitHub connector 仅在 `gh` 不可用、未认证、权限不足、功能无法完成，或用户明确要求时使用。
3. 不要对同一 Issue、PR、评论重复使用多个接口写入。
4. 中文、多行 Markdown、code fence、反引号正文必须先写 `.tmp/pr-bodies/*.md` UTF-8 文件，再用 `gh --body-file`。
5. 创建或更新 PR 后必须读回 title、body、base、head、Draft 状态并验证中文未损坏。
6. 默认基于仓库默认分支创建 `codex/<short-task>` 分支。
7. 一个任务卡对应一个分支和一个 PR。
8. Commit 必须是原子的，不机械拆分无意义 commit。
9. 提交前用 `git -c core.quotepath=false diff --name-only` 和 `git ls-files --others --exclude-standard` 核对文件。
10. 提交后用 `git -c core.quotepath=false diff --name-only origin/<base>...HEAD` 核对 PR 相对基线的 changed files。

## 工作区保护

1. 修改前先看 `git -c core.quotepath=false status --short`。
2. 不覆盖用户已有改动。
3. 不默认 stash、clean、reset。
4. 不删除工作区外文件，除非用户对具体路径给出明确授权。
5. 发现会覆盖用户已有改动时停止并报告。

## 验证选择

验证遵循先便宜后昂贵、先定向后全量、先静态后运行。

| 改动类型 | 优先验证 |
|---|---|
| 文档 | `git diff --check`、链接/引用搜索、changed files 核对 |
| Python | `python -m compileall src`、定向 unittest、相关 CLI smoke test |
| PowerShell | 语法解析、定向测试、禁止真实危险动作 |
| GitHub/PR | PR body validate、gh create/edit、gh view 读回验证 |
| hooks | 单条命令 lint、stdin JSON smoke test |

失败时记录精确命令和关键错误；不通过无限重跑掩盖不稳定测试；无代码变化时不重复运行同一个重型失败命令。

## 回报结果

完成后说明：

1. 改了哪些文件。
2. 是否触及危险操作；如果没有，明确说没有执行。
3. 运行了哪些验证命令及结果。
4. 工作区中是否存在与本次无关的既有改动。
5. 如创建 PR，说明 base/head、Draft/ready 状态和链接。
