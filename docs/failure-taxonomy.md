# 失败分类

Codex Win11 简中优化项目建议至少区分以下失败：

| 分类 | 典型表现 | 推荐处理 |
|---|---|---|
| 编码损坏 | U+FFFD、mojibake、中文 PR body 乱码 | 停止写入，回到原始事实源，UTF-8 文件重试 |
| Shell 方言错误 | PowerShell 5.1 使用 `&&`、Bash here-doc | 改成单条命令或 PowerShell 原生控制流 |
| GitHub 接口选择错误 | 本地 `gh` 可用却走 connector | 运行 `codex-win gh preflight`，记录 fallback 原因 |
| PR body mismatch | 远端 body 与本地文件不一致 | 停止并报告，不继续覆盖 |
| 工作区污染 | 任务开始已有改动或未跟踪文件 | 不 stash、不 clean，报告用户已有改动 |
| 重型重跑 | 无代码变化反复跑失败命令 | 记录关键错误，缩小复现，不无限重试 |
| 危险动作需要授权 | 注册表、服务、AppX、分区、删除工作区外文件 | 默认 dry-run/WhatIf 或停止报告 |
| 生成文件误改 | 直接改 generated output | 找 generator 或 manifest，必要时补生成流程 |
