## Why
已部署 NPU 节点使用自定义目录，SSH 向导只识别标准 HOME 布局，导致重新纳管被拒绝。
## What Changes
- 从 root 管理的独立 updater 配置识别原安装，核验配置、服务及解释器，保持原路径与业务身份。
- 检查与安装前绑定同一布局快照；拒绝遗漏设备、活动恢复记录和配置变化。
- 安装失败恢复代码、配置及 Agent/updater 文件，保持节点停用并保留证据。
- 按用户最新决定取消纳管期间自动拉取、构建与容器试运行；镜像缺失仅提供准备命令，不阻止安装，任务镜像仍按运行配置检查。
## Capabilities
### New Capabilities
- `enrollment-image-guidance`: 纳管与业务镜像就绪分离。
- `existing-node-layout`: 自定义系统安装的发现、验证与原位恢复。
### Modified Capabilities
无。
## Impact
SSH 探针、节点安装包、安装器、用户指南及测试；不改内核，不迁移资产，不更改 Worker 单元。
