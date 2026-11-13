# Proposal

## Why

用户明确授权初始化 .157 的三星 860 PRO 4TB SSD，要求各节点以相同路径访问，并将评测工作目录放在共享盘。现有存储配置可表达路径，但部署需保证共享挂载先于 Worker，防止未挂载时写到系统盘。

## What Changes

- 核对磁盘型号/序列号/容量后，仅初始化用户确认的 4TB SSD，ext4 按 UUID 持久挂载。
- 服务端及四个客户端统一 `/mnt/eval-share`；工作目录分节点，资产和缓存共享。
- NFS 只导出给已纳管节点，处理本机 UID 1001 与远程 UID 1000 的权限差异。
- Worker 改用依赖系统 mount 单元的系统服务，保留用户身份及现有本地恢复状态；增加实际挂载/卷标记启动检查。
- 验证无挂载拒绝启动、跨节点读写、容器挂载及五机任务执行；文档和 OpenSpec/Git 交付。

## Capabilities

### New Capabilities

- `shared-worker-storage`: 共享挂载一致性与 Worker 启动依赖，避免缺盘回落本地目录。

### Modified Capabilities

无 API 或数据库契约修改。

## Impact

服务端 192.168.8.157；客户端 .151/.177/.182/.183。离线 .174 待恢复后接入。格式化不可逆，只对已授权盘执行。各机器 fstab/systemd 与平台存储配置变更；数据库、Docker、token 和恢复 journal 留在本地。当前无线网络限制吞吐，不承诺高性能或高可用。
