## Why
宿主机当前依赖 Python 参数位置推断脚本，拒绝系统 Bash 与 PATH 程序；需要通用业务命令，同时保留旧模板语义。
## What Changes
新增显式 exec 命令模式，宿主机/Docker 共用最终命令解析，支持 Bash、Python、原生程序；独立业务 cwd 与平台运行器；更新界面指南与示例，增加升级准入与测试。
## Capabilities
### New Capabilities
- `generic-command-execution`: 通用 argv、cwd、环境及可执行程序解析。
### Modified Capabilities
## Impact
Worker、runner、模板校验/编辑、调度准入、用户指南。无数据库迁移；已有模板保持 legacy 行为。
