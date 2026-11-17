## Why
Worker 在注册前安装可选探测依赖，安装耗时可超过回连窗口；通用警告隐藏原因。
## What Changes
后台准备依赖并报告可用能力，回连默认 300 秒且可配置；原操作可用当前登录凭据继续验收，不重复安装或轮换节点凭据。
## Capabilities
### New Capabilities
- `node-reconnect`: resilient enrollment verification
### Modified Capabilities

## Impact
Worker、API 心跳与纳管/升级验收、执行器、SSH 向导、运维文档与测试。
