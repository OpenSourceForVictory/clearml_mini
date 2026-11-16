## Context
现有 GPU 使用用户 systemd，NPU 使用 root Worker 和普通业务账号。SSH probe 只探测 NVIDIA；root 登录不能依赖用户会话。共享存储不能通过 root 的 os.access 判定业务权限。
## Decisions
新增节点侧 host_execution 配置，仅安装管理员设置，包含 approved、user、uid、gid。新 root 安装使用 /var/lib/mini-eval/control 作为控制 HOME，代码可读但不可由业务账号写入，配置/journal保持私有；业务目录在 /var/lib/mini-eval/workspaces。mini-eval UID/GID默认29000，冲突拒绝，不修改现有其他用户。无密码、nologin、不授予 sudo/docker 组。共享存储需要管理员保持相同UID/GID；拒绝自动chown任意既有资产/NFS目录。
GPU系统服务和NPU系统服务都显式User/Group、NoNewPrivileges及清空capabilities；NPU保留设备白名单。GPU兼容旧用户服务；root缺少已批准身份时禁止宿主机执行，不能退回root业务。模板不可覆盖身份。
目录检查用真实降权子进程验证解释器、代码、cwd、工作和输出及逻辑资产；平台只调整新建执行目录的所有权，不递归改业务代码权限。权限错误包含账号、路径和用途。
root 纳管新增系统安装器，复用现有系统级Agent/updater。NPU探测复用Ascend适配器，驱动/CANN仍须预先安装，缺失明确阻止。现有旧用户安装保留兼容，跨身份迁移不复用新机器身份；不在本次部署中自动迁移整机群。
## Validation
身份拒绝root/缺失/UID变化；真实非root进程、独立Python/cwd、不可访问路径；systemd资源与清理；安装账户冲突和幂等、配置私有性、多卡服务、GPU/NPU探测、SSH预检与安装参数；关联旧GPU和NPU回归、UI指南、隔离实际安装验证。
