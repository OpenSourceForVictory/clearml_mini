# GPU 节点共享 SSD

2026-11-13 部署：192.168.8.157 提供 Samsung SSD 860 PRO 4TB，序列号 `S5G9NC0N601269W`，设备初始化时为 `/dev/sda`。经用户确认清空后建立 GPT、单 ext4 分区；SMART 健康通过，无重映射扇区。原始容量 4,096,805,658,624 字节，格式化后约 3.7 TiB，预留 1%。其他磁盘未改动。

卷 UUID：`7bba06cb-9585-4d5a-8da8-57232b8a1cfa`。服务端 fstab 按 UUID 挂载，不依赖设备名。四个客户端使用 NFSv4.2；五机统一路径 `/mnt/eval-share`。

| 节点 | 工作基目录 |
|---|---|
| local4090-151 | /mnt/eval-share/work/local4090-151 |
| rtx8000-177 | /mnt/eval-share/work/rtx8000-177 |
| rtx3080-182 | /mnt/eval-share/work/rtx3080-182 |
| rtx4090-183 | /mnt/eval-share/work/rtx4090-183 |
| rtx4090-157 | /mnt/eval-share/work/rtx4090-157 |

每个工作基目录由 Worker 继续追加 `slots/<GPU slot ID>/runs/<execution ID>`，避免不同节点/执行互相覆盖。统一资产 `/mnt/eval-share/assets`，统一缓存 `/mnt/eval-share/cache`；最低可用空间 20 GiB。平台机器管理可查看配置与 applied 回执。资产初始为空，本次没有安装完整 RoboTwin 资产。共享缓存中的客户端自定义文件仍应使用独立键和原子写入，不能假设多个客户端可以并发改写同一文件。

数据库、Docker 镜像、Worker token 与恢复 journal 保留本机；旧工作目录和历史输出未删除或自动迁移。离线的 192.168.8.174 尚未加入。

## 挂载与权限

服务端配置 `/etc/exports.d/mini-eval.exports` 仅允许 .151、.177、.182、.183，使用 `rw,sync,no_subtree_check,root_squash,mountpoint`。NFSv3/UDP 关闭，NFS 监听 192.168.8.157。`root_squash` 的匿名 UID/GID 按客户端 Worker 用户映射：.151 为 1001，其余 1000，使 Docker root 能访问该 Worker 的私有执行父目录；未启用 no_root_squash。工作目录 0700；资产/缓存归 UID1000，ACL 给 UID1001 写入及默认继承权限。

这是可信局域网的 AUTH_SYS 文件共享，IP/UID 限制不构成对恶意节点或多租户的强隔离；不同远程机器同为 UID1000。NFS 无传输加密，不应将 2049 暴露公网。新增节点需要同步 export 白名单、UID 权限、客户端 fstab 和 Worker 单元。

客户端 fstab：

```fstab
192.168.8.157:/mnt/eval-share /mnt/eval-share nfs4 rw,hard,_netdev,nofail,nfsvers=4.2,timeo=600,retrans=2,x-systemd.mount-timeout=30s 0 0
```

未挂载时目录由 root 持有且 0555，Worker 不能在系统盘创建任务数据。NFS 采用 hard 挂载避免网络中断被当作正常写入失败；网络断开时已有 I/O 可能等待恢复，mount 仍存在时 systemd 不会自动判断服务器失联。当前节点通过 Wi-Fi 互联，吞吐与延迟受网络影响；.157 关机或 SSD 故障会影响所有节点。共享盘不是备份，关键结果需另行备份。

## Worker 启停与挂载检查

当前五机使用系统单元 `mini-eval-worker-nfs.service`，以原 dgh/huawei 身份运行并加入 docker 补充组。旧用户单元 `mini-eval-worker@fleet-gpu0.service` 已停用，避免重复 Worker。程序、token 和 state_path 均沿用原路径，GPU slot 身份不变。

```bash
sudo systemctl status mini-eval-worker-nfs
sudo journalctl -u mini-eval-worker-nfs -n 80
sudo systemctl restart mini-eval-worker-nfs
findmnt --mountpoint /mnt/eval-share
df -h /mnt/eval-share
```

系统单元有 `RequiresMountsFor=/mnt/eval-share`、`BindsTo=mnt-eval\x2dshare.mount` 和对应 After。启动前 `/usr/local/lib/mini-eval-check-shared-mount.py` 检查精确挂载点、来源、文件系统、rw 和 `.mini-eval-volume-id`。服务端按 UUID 校验，客户端按 `192.168.8.157:/mnt/eval-share` 校验。检查器源码为 `scripts/check_shared_mount.py`。错误来源或标记、只读挂载、缺失挂载均拒绝启动；服务端 NFS 服务也依赖该 SSD 的 mount，export 带 mountpoint 条件。

计划维护先排空任务、停止各 Worker，再卸载客户端，最后停 NFS 服务/服务端卷。恢复时先挂服务端盘、启动 nfs-server，再挂客户端并启动 Worker；因依赖卸载而停止的 Worker 应显式启动。不要对运行任务使用强制/懒卸载。

## 回退与配置备份

服务端格式化前 fstab/设备信息位于 `/root/mini-eval-nfs-backup-20261113-105937`；各节点挂载配置备份位于 `/root/mini-eval-nfs-config-backup`。本机 `.runtime/nfs-storage/storage-before.json` 保存平台旧目录配置，运行证据亦在该目录，不能整体提交 Git。

回退时排空任务并停新系统 Worker，通过平台设置原本机目录（使用最新 expected_revision），确认旧数据保留，再启用原用户 Worker 服务。原 config/token/journal 不需要删除。原三台新增 Docker 节点的用户服务 docker-group drop-in 仍保留。确认没有进程使用共享盘后再移除 NFS fstab/export/drop-in。备份只能恢复配置，无法撤销本次已授权格式化或恢复格式化前数据。

## 验收结果

五节点目录配置均 applied；重启后原 5 个 GPU slot ID 保持且全部在线空闲。跨节点写入/可见性检查通过，Worker 的受限容器存储探测通过。共享 SSD 内找到全部 7 份 report.json，覆盖五个工作子树。7 项 SDK 协议自检观察到 5 并发 + 2 排队，最终 7/7 成功且回合报告齐全；仅验证平台链路，不代表 RoboTwin 模型评测。批次 ID：`e02e8f37-7ad6-45aa-a663-1676758c91f0`。临时推理服务已关闭，自检模板已禁用。

真实卸载测试确认本机 Worker 自动停止、启动检查拒绝缺失挂载、底层目录不可写；恢复挂载和 Worker 后在线。错误来源检查拒绝通过。相关回归 27 项通过，1 项需要 STORAGE_PROBE_TEST_IMAGE 的可选 Docker 测试跳过；本次五机真实存储探测及任务容器执行均已通过。
