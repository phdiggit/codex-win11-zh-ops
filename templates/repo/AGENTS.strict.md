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

1. 用户可见文档、注释和报告默认使用中文；schema 字段名、manifest 键名、函数名、参数名和路径变量名保持英文。
2. 中文 Markdown、JSON、PR body、评论正文不得通过 PowerShell inline、管道或 here-string 传递；先写 UTF-8 文件，再交给目标程序。
3. 处理中文 Markdown、JSON 或文本编码时，优先运行 `codex-win encoding check <path>`；需要写 JSON 时优先用 `codex-win encoding write-json <path> --input <json-file>`。
4. Windows PowerShell 5.1 不使用 `&&`、`||` 或 Bash here-doc；连续步骤拆成单条命令，或使用 PowerShell 原生控制流；准备执行复杂命令前，优先运行 `codex-win shell lint --shell powershell5 --command "<command>"` 检查 shell 方言和 inline `gh --body` 风险。
5. Windows 上运行 Python、pytest、validator、export、build 等子进程时，优先使用 `codex-win run -- <command...>`，让子进程继承 `PYTHONUTF8=1` 和 `PYTHONIOENCODING=utf-8`。
6. Git 状态和 diff 核对使用 `git -c core.quotepath=false`。
7. `.ps1` 文件如需要兼容 Windows PowerShell 5.1 中文输出，可使用 UTF-8 BOM。
8. 自动化目录尽量使用英文、数字、短横线，避免 WinPE、cmd、SMB 和日志编码问题。
9. 只有在 `codex-win` 不可用、当前任务不属于以上风险场景，或用户明确要求时，才直接使用原生命令替代；工具不可用时先报告原因，再使用最小等价命令，不静默降级。

## GitHub、Commit 与 PR

1. 接手仓库或准备 GitHub 操作前，优先运行 `codex-win preflight` 或 `codex-win gh preflight` 判断本地 `gh`、认证和仓库状态。
2. 当前仓库本地存在且 `gh` 已认证时，GitHub 远端读写默认优先使用 `gh`；connector 仅在 `gh` 不可用、未认证、权限不足、功能无法完成，或用户明确要求时使用。
3. 不要对同一 Issue、PR、评论重复使用多个接口写入。
4. 中文、多行 Markdown、code fence、反引号正文必须先写 `.tmp/bodies/*.md` UTF-8 文件，先运行 `codex-win body normalize/validate`，再用 `gh --body-file`；PR body 也可使用兼容入口 `codex-win pr-body ...`。
5. 创建、编辑或验证 PR 时，优先使用 `codex-win gh pr-create/pr-edit/pr-verify`；如果直接使用 `gh`，仍必须先通过 `codex-win body validate`，并读回 title、body、base、head、Draft 状态验证中文未损坏。
6. 需要 PR Review Package 时，可用 `codex-win review-pack --pr <pr> --base <base> --output .tmp/review-pack.md` 生成机械事实层；它只辅助核对 HEAD snapshot、scope 和协议，不替代 reviewer 的 findings、风险判断或 merge 决策。
7. 阶段性或部分交付 PR 使用 `Refs #<issue>`；只有任务卡明确允许完整关闭时才使用 `Closes/Fixes/Resolves #<issue>`。
8. 默认基于仓库默认分支创建 `codex/<short-task>` 分支；一个任务卡对应一个分支和一个 PR。
9. Commit 必须是原子的，不机械拆分无意义 commit。
10. 提交前用 `git -c core.quotepath=false diff --name-only` 和 `git ls-files --others --exclude-standard` 核对文件；提交后用 `git -c core.quotepath=false diff --name-only origin/<base>...HEAD` 核对 PR 相对基线的 changed files。

## 工作区保护

1. 修改前先看 `git -c core.quotepath=false status --short`。
2. 不覆盖用户已有改动。
3. 不默认 stash、clean、reset。
4. 不删除工作区外文件，除非用户对具体路径给出明确授权。
5. 发现会覆盖用户已有改动时停止并报告。

## 验证选择

验证遵循先便宜后昂贵、先定向后全量、先静态后运行。需要 pytest 或同类全量验证前，先用 `codex-win test plan --base <base> --head HEAD` 判断 focused tests、full pytest 是否必要，以及当前 head SHA 是否已经跑过 full pytest；测试预算状态默认写入 `.tmp/codex-test-plan-state.json`。

| 改动类型 | 优先验证 |
|---|---|
| 文档 | `git diff --check`、链接/引用搜索、changed files 核对；修改 AGENTS 或模板后运行 `codex-win agents lint <path>` |
| Python | `codex-win run -- python -m compileall src`、定向 unittest、相关 CLI smoke test；full pytest 同一 head SHA 最多运行一次 |
| PowerShell | `codex-win shell lint --shell powershell5 --command "..."`、语法解析、定向测试、禁止真实危险动作 |
| GitHub/PR | `codex-win body validate`、`codex-win gh pr-create/pr-edit/pr-verify`、gh view 读回验证 |
| hooks | `codex-win shell lint`、stdin JSON smoke test |

失败时记录精确命令和关键错误；不通过无限重跑掩盖不稳定测试；无代码变化时不重复运行同一个重型失败命令。只有当前 head 的 full pytest 失败、且需要判断是否为基线问题时，才运行 base full pytest。

生成物清理遵循“先记录验证结果，再清理一次”：使用 `codex-win cleanup generated --profile markdown-exports --target <repo>` 先 dry-run，确认只命中显式配置的生成路径后才加 `--apply`；默认保留 `**/.gitkeep`；不要在同一轮验证中反复生成、清理、再生成。

## 回报结果

完成后说明：

1. 改了哪些文件。
2. 是否触及危险操作；如果没有，明确说没有执行。
3. 运行了哪些验证命令及结果。
4. 工作区中是否存在与本次无关的既有改动。
5. 如创建 PR，说明 base/head、Draft/ready 状态和链接。


## Strict profile 追加规则

1. 本地 `gh` 已认证时，禁止优先使用 GitHub connector；connector 只能作为失败后的 fallback。
2. 所有中文 PR body、Issue body、评论正文、release notes 都必须通过 `codex-win body normalize/validate` 或项目等价工具。
3. PowerShell 中出现 `&&`、`||`、Bash here-doc、`gh --body "..."` 视为必须修正的命令错误。
4. 生成文件默认不直接编辑；先寻找 generator、manifest 或源文件。
5. 如果验证需要真实危险动作，必须停止并报告，不用假验证冒充通过。
6. Windows 子进程默认通过 `codex-win run -- ...` 执行；只有非 Python、不会读写中文且无编码风险的轻量命令才可直接运行。
7. 同一 head SHA 的 full pytest 不得重复运行；base full pytest 只用于当前 head full pytest 失败后的基线分类。
8. 生成物清理只在记录验证结果后执行一次，必须先 dry-run 并确认命中路径来自显式 cleanup profile。
