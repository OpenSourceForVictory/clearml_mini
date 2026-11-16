## Why
统一 root 纳管和节点管理，避免不同 SSH 用户造成 systemd 和目录权限差异；宿主机业务必须使用固定普通身份。
## What Changes
- root 新节点安装使用系统服务和独立私有控制目录，创建 mini-eval 业务账号。
- GPU/NPU 系统 Worker 统一普通身份启动、业务身份目录检查和资源验证。
- SSH 向导支持 root 与 GPU/NPU 选择，展示管理/业务身份与检查错误。
- 旧用户服务保持兼容，不在升级时静默迁移；已有节点迁移需排空并明确执行。
## Capabilities
### New Capabilities
- root-managed-host: 管理身份和业务身份分离的宿主机执行与纳管。
## Impact
安装包、SSH执行器、Worker宿主机启动/探测、节点身份与目录权限、前端纳管指南及测试。
