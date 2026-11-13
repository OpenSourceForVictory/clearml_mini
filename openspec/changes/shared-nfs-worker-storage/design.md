# Design

## Context

已纳管 .151/.177/.182/.183/.157，均无运行评测。用户授权初始化 .157 的 4TB Samsung SSD 860 PRO（序列号 S5G9NC0N601269W），其余盘不改。Worker 用户本机 dgh UID 1001，其余 huawei UID 1000；容器挂载检查使用 UID 65534。现有工作/输出子目录模式 0777，执行父目录 0700。

## Goals / Non-Goals

Goals: ext4 + NFSv4、同路径挂载、节点工作目录、启动身份保护、真实任务验收。
Non-Goals: 不部署分布式文件系统/RAID，不迁移数据库或 Docker 数据，不复制全部旧任务数据，不保证 Wi-Fi 性能或 NFS 高可用。

## Decisions

- .157 以 GPT 单分区 ext4 挂载 /mnt/eval-share，fstab 使用 UUID；服务端不用回挂自己的 NFS。
- NFS 仅授权四个客户端 IP；使用 sync、no_subtree_check、mountpoint、root_squash。为容器 root 经 NFS 遍历 Worker 的 0700 执行目录，按客户端把匿名 root 映射到该节点 Worker UID/GID（.151 为 1001，其余 1000），不启用 no_root_squash。这是受信任机器的 AUTH_SYS 内网共享，不是恶意客户端隔离边界。
- /mnt/eval-share/work/<node> 归该节点 UID，assets 和 cache 为可读公共目录；容器仍只读挂载 assets/cache，输出留在各自工作树。
- 原用户 Worker 改成同用户运行的系统服务，保留原配置/状态路径，以 RequiresMountsFor、BindsTo 和 After 依赖系统挂载，避免用户服务无法可靠引用系统 mount。挂载根未挂载时 root:root 0555，不允许 Worker 创建本地替代工作树。
- ExecStartPre 使用独立标准库检查器：findmnt 精确 mountpoint、来源、文件系统，卷 UUID 标记一致，文件可读；不接受只检查目录存在。NFS hard mount 保证 I/O 不因短超时静默失败，服务失联仍可能阻塞任务，文档说明人工恢复步骤。
- NFS 服务也依赖实际 SSD mount，防止磁盘失效时导出系统盘上的空目录。

## Risks / Trade-offs

- 格式化不可逆 → 型号、序列号、容量、无挂载再次核验后仅操作授权盘，保留盘点证据。
- root_squash 与容器访问冲突 → 用节点 UID 映射及真实 Docker 挂载/读写验证；不放开服务端 root 权限。
- 私网断线 → hard NFS 可等待恢复，不承诺运行中即时故障隔离；挂载消失则 systemd 停止 Worker。
- 系统启动时 NFS 不可达 → Worker 不运行；恢复 mount 后再启动 Worker。

## Migration Plan

备份 fstab、服务和平台存储配置；核对无持有执行；安装 NFS 包；格式化授权盘并配置服务端；客户端挂载；停止旧用户 Worker，安装系统单元；通过平台切换目录；恢复 Worker 并验收。旧本地工作树和本地 journal 保留。回退先排空任务、恢复旧存储配置、停止新系统单元、恢复原用户单元；新增 SSD 数据不删除，格式化前内容无法保证恢复。
