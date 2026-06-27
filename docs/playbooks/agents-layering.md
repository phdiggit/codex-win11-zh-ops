# AGENTS 分层 Playbook

## 三层结构

1. 全局 AGENTS：个人机器稳定事实，例如 Win11 简中、PowerShell 5.1、gh 优先。
2. 项目根 AGENTS：仓库生命周期、目录职责、验证入口、危险边界。
3. 子目录 AGENTS：docs/scripts/generated 等局部强约束。

## 根 AGENTS 应该写什么

- 稳定原则。
- 任务启动协议。
- 最小读取路由。
- Shell/编码/GitHub/PR 规则。
- 工作区保护。
- 验证入口。
- 最终报告字段。

## 根 AGENTS 不应该写什么

- 当前 Issue 状态。
- 当前分支名或临时 SHA。
- 当天日志。
- 个人机器绝对路径，除非是全局 AGENTS。
- 大段业务文档或完整失败日志。

## 子目录 AGENTS 示例

- `docs/AGENTS.md`：事实源、Markdown 规则、文档验证。
- `scripts/AGENTS.md`：脚本职责、编码、危险动作、测试入口。
- `generated/AGENTS.md`：默认不得直接编辑，先修改 generator。
