## Why
EulerOS v1/混合 cgroup 节点被 root v2 检查拒绝，缺少 runner 镜像还需用户手动准备。
## What Changes
- Root 系统宿主机支持 v1/v2/混合层级，以实际 CPU/内存/PID 限制验证准入。
- Docker 不依赖宿主机 systemd 资源探针，确认计划后自动准备 runner 并验证容器限制、NPU 设备访问。
- 自定义镜像只拉取，默认 runner 拉取失败从平台可信包构建，错误和阶段界面可见。
## Capabilities
### New Capabilities
- `node-runtime-compatibility`: 多层级资源验证与自动镜像准备。
### Modified Capabilities
无。
## Impact
纳管、安装器、Worker 内核限制验证与版本、前端、指南及关联测试；不改内核引导、不重启主机，不自动升级所有节点。
