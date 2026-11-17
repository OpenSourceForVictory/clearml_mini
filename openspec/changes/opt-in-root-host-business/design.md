## Decision
仅在节点本地 `host_execution` 同时包含 `approved: true`、`user: root`、`uid: 0`、`gid: 0` 和 `allow_root_business: true` 时允许 root 业务进程。没有标志或普通账号误设标志均拒绝。NPU 还要求 `npu_runtime.host.approved: true` 和同一 `user: root`。这不是模板字段，也不从 SSH 登录身份推断。

systemd 仍保留 User/Group、NoNewPrivileges、设备 DevicePolicy 与任务 cgroup 额度；root 分支将 capabilities 限制为 DAC_OVERRIDE 和 DAC_READ_SEARCH，以便访问本地受限目录，其他 capabilities 清空。root 业务可读取节点上其他文件，包括平台凭据，管理员只能在可信专用节点使用。NFS root_squash 由存储端决定，平台不能绕过。

配置变更需要先排空任务、备份并修改每张 Worker 的本地 JSON、重启 Worker，再确认宿主机环境探测。安装器新装仍默认 mini-eval，重新纳管保留原配置，不自动把旧节点切为 root。
