# 五节点发布与验收

OpenSpec：`production-fleet-acceptance`。本文件随实施更新；盘点通过不等于已经发布。

## 只读盘点与计划

```bash
python3 scripts/fleet_release.py inventory --output .runtime/fleet-release/inventory.json
python3 -m scripts.fleet_release plan --inventory .runtime/fleet-release/inventory.json --output .runtime/fleet-release/plan.json
```

目标固定为 192.168.8.151、.157、.177、.182、.183，其他地址拒绝执行。远端使用现有 SSH 配置，强制批处理和已知主机密钥校验。盘点只读主机身份、版本常量、指定 systemd 服务、GPU 和共享挂载；不输出环境或凭据。每个探测子命令最多 8 秒，每台总预算 55 秒。任何探测未知都不能计为通过。

报告原子写入且权限 0600。计划绑定 machine-id、GPU 和挂载信息，执行变更前必须重新盘点比较，不能直接信任历史记录。计划不包含凭据。

发布阶段记录采用同一目录下的机群锁，不同 release ID 也不能同时执行。步骤以输入摘要绑定，完成项可幂等跳过；开始但未完成的步骤必须先核验现场状态，不能盲目重放。所有发布进程须使用同一状态目录。当前 CLI 只提供 inventory/plan，变更执行入口尚在实现。

## 2026-11-16 发布前基线

通过严格 SSH 主机校验访问四台远端并读取本机；五机真实设备和平台 GPU UUID 一致。

| 组件 | 当前实测 | 目标 |
| --- | --- | --- |
| API 镜像 | mini-eval-api:agent-fleet-recovery | 本次提交构建的固定制品 |
| 数据库 | n407c89 | r841a23 |
| 五台 Worker | 0.10.0 / 协议 9 | 0.12.0 / 协议 10 |
| 五台 Agent | 1.0.0 | 1.1.0 |

五台 Worker、Agent、升级执行器均运行；目录修订 .151/.177 为 2，其余为 3，平台回执均匹配。工作基目录在 `/mnt/eval-share/work/<节点名>`；.157 为本地 ext4，其余为来自 .157 的 NFS。盘点时活动与待清理执行为零，历史执行 25 成功、2 失败。该数量仅是盘点时快照，发布前必须重查。

当前 Compose 包含 LAN overlay 与独立镜像 overlay；发布不得仅执行默认 `control.py up` 而遗漏内网监听。原始脱敏盘点保存在 `.runtime/fleet-release/inventory.json`（不入库）。未访问 .174；未改变节点或平台状态。

## 排空与准入恢复

`scripts.fleet_release_api` 通过现有管理 API 操作；`drain` 使用管理修订号停用新准入，等待 `held_executions=0`，不会撤销执行租约或杀任务。超时停止发布，调用 `restore_admission` 恢复本次成功记录的变更。原本停用的节点保持停用；管理员期间修改修订号、请求响应丢失或节点身份改变时拒绝自动恢复，需要对账。

`assert_release_ready` 核验每个 GPU Worker 在线、软件版本、协议、制品摘要，以及 Agent 在线、版本、摘要和配置应用状态；任一不符都不能通过发布验收。原有 claim 协议兼容检查继续负责阻止旧 Worker 领取新编排。

模块测试同时包含真实 API 集成：运行中的任务在排空期间继续获得心跳续租，且不会领到新任务。测试使用隔离数据库，不操作生产节点。
