## Why
受 NFS/本地目录属主限制，部分可信宿主机业务程序无法由默认 mini-eval 账号访问其代码或依赖。节点管理员需要显式选择以 root 运行宿主机业务；模板不能替节点扩大身份权限。

## What Changes
- 节点本地 host_execution 允许管理员明确批准 root 业务身份，默认和旧配置仍使用普通账号。
- NPU 宿主机同时要求 npu_runtime.host.user 与 host_execution.user 一致，沿用设备 cgroup 限制；root 业务只保留访问文件所需的 DAC capabilities。
- 身份与权限探测、systemd 任务启动及 CANN 初始化使用相同业务身份；界面和运维文档说明配置、风险与 NFS root_squash 边界。

## Impact
Worker 宿主机身份、NPU 启动、节点本地配置与定向测试；不改数据库和公开任务 API。Docker 用户由镜像独立决定。
