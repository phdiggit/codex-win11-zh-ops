# scripts/AGENTS.md

适用于 `scripts/` 目录。

1. 脚本职责应清晰，不为一个功能新增多层 wrapper。
2. Python 脚本读写中文文件必须显式 `encoding="utf-8"`，JSON 写入使用 `ensure_ascii=False`。
3. PowerShell 5.1 兼容脚本不得使用 `&&`、`||` 或 Bash here-doc。
4. `.ps1` 如包含中文注释或中文输出，并要求 Windows PowerShell 5.1 可靠读取，可使用 UTF-8 BOM。
5. 涉及注册表、服务、Defender、AppX、Junction、分区、镜像等危险动作的脚本必须支持 dry-run、WhatIf 或 plan 模式。
6. 普通验证不得执行真实安装、卸载、服务变更、注册表写入、AppX 卸载、分区、格式化或工作区外删除。
7. 修改脚本后先做语法检查，再运行最小定向测试。
