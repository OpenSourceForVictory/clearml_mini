# 五节点发布与验收

OpenSpec：`production-fleet-acceptance`。本文件随实施更新；盘点通过不等于已经发布。

## 只读盘点与计划

```bash
python3 scripts/fleet_release.py inventory --output .runtime/fleet-release/inventory.json
python3 -m scripts.fleet_release plan --inventory .runtime/fleet-release/inventory.json --output .runtime/fleet-release/plan.json
```

目标固定为 192.168.8.151、.157、.177、.182、.183，其他地址拒绝执行。远端使用现有 SSH 配置，强制批处理和已知主机密钥校验。盘点只读主机身份、版本常量、指定 systemd 服务、GPU 和共享挂载；不输出环境或凭据。每个探测子命令最多 8 秒，每台总预算 55 秒。任何探测未知都不能计为通过。

报告原子写入且权限 0600。计划绑定 machine-id、GPU 和挂载信息，执行变更前必须重新盘点比较，不能直接信任历史记录。计划不包含凭据。

发布阶段记录采用同一目录下的机群锁，不同 release ID 也不能同时执行。步骤以输入摘要绑定，完成项可幂等跳过；开始但未完成的步骤必须先核验现场状态，不能盲目重放。所有发布进程须使用同一状态目录。`scripts.fleet_rollout` 提供 check/apply/resume/restore-admission；阶段记录与目标镜像 ID 及计划摘要绑定；隔离启动候选镜像核对 Worker/Agent/协议及 Alembic head，版本不符在排空前拒绝。

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

## 备份恢复与逐节点升级

`scripts.fleet_release_control` 将 Compose 的基础配置、LAN overlay 和固定镜像配置作为完整发布输入。备份以独占创建、0600 权限落盘并计算 SHA-256；`restore_probe` 先校验摘要，然后在无外部网络的临时 PostgreSQL 容器恢复，使用目标镜像执行迁移，比较迁移前后历史执行数量。恢复或迁移失败会终止，不覆盖生产数据库。

本次预演：生产快照从 n407c89 恢复并升级至 r841a23，27 条执行记录保持不变；临时恢复容器已删除。正式迁移前需在排空状态重新生成备份，预演备份不能替代最终备份。

`upgrade_nodes` 要求整个目标集合已停新准入且无持有执行，通过现有升级 API 逐节点发起；每个节点成功后复核所有 Worker 与 Agent 的版本和摘要，才继续下一个。共享存储主机 .157 最后处理。失败、回滚、超时或不确定响应即停止后续节点；不通过删 journal 或取消尚未确认的安装恢复准入。

## 验收对象登记

`scripts.fleet_acceptance.Registry` 将测试批次、模板及所有者写入独立清单，重启后复用同一测试 ID。取消和停用操作先检查清单归属及平台实际名称，不接收未登记 ID。模板停用可重复执行；执行与服务清理由平台完成，验收工具不使用裸 PID 杀进程。

故障回归使用隔离 API 数据库与临时 systemd 单元/容器：`HOST_EXECUTION_TEST=1` 配合 `test_host_backend.py`、`test_service_recovery.py`；Agent/资源/存储故障由 `test_agent_faults.py`、`test_agent_integration.py`、`test_resource_reporting.py`、`test_worker_storage.py` 覆盖。它们不是生产 NFS 停机或五机断网验收，报告必须分别标注。


## 发布命令

管理员 token 文件为本地 0600 JSON（字段 `token`），不传在命令行，不入库。先执行只读检查：

```bash
python3 -m scripts.fleet_rollout check --plan .runtime/fleet-release/plan.json --token-file .runtime/fleet-release/auth.json --state-directory .runtime/fleet-release/next-release --release-id next-release --image mini-eval-api:approved-release
```

检查通过后，将 `check` 改为 `apply` 执行。执行时固定本地不可变镜像 ID，标签重新指向其他制品不会改变本次发布输入；没有镜像绑定的旧手工阶段记录不能由新 CLI 自动续跑。控制端监听、JWT/数据库配置、资源额度与现有运行配置不一致时拒绝继续；工具不打印敏感配置差异。`resume --reconcile-node 节点名` 只对已有升级回执、指定制品和已成功且就绪的节点完成对账，不重新安装不确定操作。数据库迁移或交换镜像期间发生不确定故障时，需要先检查数据库版本与控制端日志，不能盲目重放。`restore-admission` 也要求目标版本和配置回执全部通过，不会把不确定节点重新放入调度。

发布保持原节点 ID、GPU UUID、NFS 挂载与工作路径。旧 API 镜像和节点升级器生成的 `.mini-eval-backup-<upgrade-id>` 保留。生产已产生新测试记录，不能直接用发布前数据库快照覆盖当前库；恢复旧版本需先排空并确定兼容迁移方案。

## 本次实际发布

2026-11-16：控制端已迁移至 `r841a23`，镜像 `mini-eval-api:fleet-20261116`；五台 Worker 为 0.12.0/协议 10，Agent 为 1.1.0，统一制品摘要 `ac5223fda0175dc815891fecea7a03f76acf0f2cbbb6f0fc4165b7c7ca354de0`。升级后存储、运行环境、宿主机配置回执均已检查，原准入状态恢复。

首次逐节点检查遇到 Agent 注册成功而配置回执尚在途，工具停止后续升级；修正为期限内等待已应用回执，并核验原升级 ID 后继续，没有重复安装。首次旧 Worker 冒烟脚本读取旧单执行 journal，已改为显式隔离状态目录和按任务查找多执行 journal，并重跑成功。

## 真实验收命令

`python3 -m scripts.smoke_fleet_orchestration` 提供 prepare/matrix/soak/cleanup。必须显式给出 token 文件、独立目录和测试 ID。prepare 保存原节点配置，仅增加测试宿主机环境、镜像许可及 .157 的服务角色和端口范围；cleanup 待所属执行清理后按修订号恢复，不覆盖期间管理员修改。

matrix 覆盖四种 host/docker 配对 × 共享/独占服务，每组两个三任务批次；使用真实 HTTP 参数回显和 SDK 逐回合结果。另以真实 CUDA 分配验证五台 GPU；这些测试不代表 RoboTwin 模型效果。

soak 需要独立测试目录、专用外部回显服务地址和私密管理员登录文件以更新 30 分钟有效的 token；至少两小时、三用户、30 个三任务批次，周期记录心跳和排队推进。测试账号使用随机密码，收尾停用。生产 NFS、Docker daemon 和整机网络不做破坏性操作。

`scripts.smoke_fleet_controls` 验证暂停/恢复/取消/单项及批量失败重试；`scripts.smoke_fleet_admission` 验证 CPU/RAM 超额请求拒绝和显存不足排队。CPU/RAM 压力及过期遥测的调度分支由隔离测试覆盖，未用真实机器内存耗尽制造故障。真实浏览器用例 `fleet-production.spec.ts` 必须显式设置 `FLEET_ACCEPTANCE_USERS` 才会运行。
