## Why
新机器仅启动平台会缺少 SSH 执行器，用户被迫手动安装服务和配置 socket。
## What Changes
- 默认部署自动启动独立 SSH 执行器，包含依赖、健康检查、持久状态和重启策略。
- API 自动连接，保留旧宿主机执行器配置兼容。
- 注册界面展示状态、断连禁用检查并自动重试，用户无需终端配置。
## Capabilities
### New Capabilities
- `bundled-onboarding`: 默认纳管执行器部署。
### Modified Capabilities
无。
## Impact
Compose、执行器健康检查、机器注册界面、部署文档与测试；不改变目标节点或纳管授权协议。
