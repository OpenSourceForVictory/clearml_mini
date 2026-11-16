## Why
现有覆盖率仅覆盖少数新增模块，无法评估目录与关键功能缺口，需要可重复基线和持续检查。
## What Changes
为 Python 项目建立全目录分支测量、按目录及功能聚合的报告、基线门槛、CI 产物与操作文档，补报告算法测试；修复全量测试发现的协议 14 注册与 SDK 下载包遗漏。补齐执行、纳管、构建和运维异常测试，保留 SSH 响应超限诊断；明确前端与独立进程未测边界。
## Capabilities
### New Capabilities
- `coverage-reporting`: 可复现覆盖率报告与按目录防退化门槛。
### Modified Capabilities
## Impact
测试工具、CI、开发说明与 Skill；包含注册协议兼容、SDK 包完整性及纳管错误分类修正，不自动部署生产。
