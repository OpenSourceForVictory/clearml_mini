# 节点 Agent

每台物理节点安装一个节点 Agent。平台决定调度、资源分配和业务重试；Agent 管节点，Worker 执行评测或推理服务。当前逐 GPU Worker 继续作为兼容执行组件，不代表已经转换成一个多卡执行进程。

## 模块和责任

| 模块 | 文件 | 输出及依赖 |
| --- | --- | --- |
| 平台连接 | node_agent/transport.py | 身份认证、禁用重定向、有界响应和超时，连接失效终止控制 |
| 本地状态 | node_agent/state.py | 单实例锁、原子落盘、最多64条固定事件、序号；拒绝共享控制目录与损坏状态 |
| 监测 | node_agent/monitor.py | CPU/内存、GPU、Docker、本地磁盘；独立探测线程，不阻塞心跳 |
| 程序适配 | node_agent/programs.py | 安装时登记的 Worker systemd单元；无任意命令接口，启动失败限制重试频率 |
| 状态协调 | node_agent/client.py | 配置修订、独立控制线程、排空许可、升级期间暂停协调 |
| 平台协议 | backend/app/node_agents.py | 会话、心跳序号、配置意图、停止屏障、准入 |
| 任务/服务执行 | worker/client.py、worker/host_backend.py、worker/services.py | 现有租约、journal、Docker/host、服务健康和执行清理 |
| 工作区及日志 | worker/storage.py、worker/workspace.py、worker/retention.py、runner/sdk.py | 节点目录配置、NFS要求、容量、任务日志/回合/结果，保留原协议 |
| 独立升级器 | worker/updater.py | 停止Agent与Worker、校验包、换版本、回连验证、失败回滚 |

数据流：平台期望配置 → Agent协调 → 固定程序适配器 → Worker → Docker/host任务。监测和程序状态 → Agent心跳 → 平台。业务SDK → 现有任务事件接口 → 回合与结果汇总。Agent的在线不能代替执行租约或GPU就绪。

## 配置与状态

机器管理的配置详情包含“节点 Agent”：在线/版本、最近心跳、期望及已应用修订、受管程序、监测和近期事件。

首版 Agent 配置包含心跳周期（2–15秒）、采样周期（5–120秒）、Worker运行/排空后停止。工作目录、资产、镜像和宿主机环境仍使用原有配置面板及各自回执，避免重复配置源。

排空并停止 Worker 先阻止新任务准入，释放未领取预留，再等待活动和未清理执行清零。Agent每次停止前申请带会话和修订的许可。停止回执前不能恢复运行。恢复Worker不会自动启用停用机器，也不会改变资源池归属。

接口：
- `POST /api/v1/agent/register`：节点凭据，boot_id/version/digest，获得会话。
- `POST /api/v1/agent/heartbeat`：会话、递增sequence、applied_revision/config_status、samples/programs/events；返回配置和事件确认序号。
- `POST /api/v1/agent/stop-permit`：会话和revision；只有当前停止意图、无持有执行且无升级屏障才允许。
- `GET/PUT /api/v1/admin/nodes/{id}/agent`：管理员读状态/更新配置，PUT要求expected_revision。

心跳超过45秒显示离线，新节点Agent未回连或配置未应用时阻止新任务。旧节点没有Agent记录则继续原有Worker准入。重复上报不重复追加事件；旧会话返回409并停止旧Agent。节点事件只包含固定代码，不传原始子进程输出、密码或命令参数。任务/推理日志在各自实例页查看。

## 安装

完整安装包包含 `node_agent/` 和 `scripts/install_node_agent.py`。`scripts/install_node.py` 新装和重新纳管时安装Agent并配置独立升级器，不修改原Worker单元及NFS依赖。

在已安装节点手动部署时，使用包含 `app_dir/state_dir/config_paths/services` 的原升级器配置：

```sh
python3 scripts/install_node_agent.py --config /绝对路径/节点升级配置.json --start
```

服务名为 `mini-eval-agent.service`，服务scope沿用Worker配置。system服务需以root运行，user服务需对应用户的systemd用户总线。之后必须将生成的 `agent` 条目加入独立升级器配置；推荐使用完整安装器自动完成，单独运行脚本只安装Agent，不代表独立升级器已更新。

Agent配置和恢复文件位于原升级状态目录的同级 `mini-eval-agent/`，凭据文件权限600，目录700。控制目录必须位于本地磁盘，不能使用NFS/CIFS/SSHFS或符号链接路径。评测工作区和资产继续允许NFS。

## 升级与故障

发布包覆盖Worker、runner、node_agent。升级器先停Agent再停Worker；启动时先Worker后Agent，原服务单元不重写。配置了Agent的节点需Agent与全部GPU上报目标摘要才能成功。换版本失败恢复整个旧目录；回滚失败继续保持不可调度。升级器本身仍独立于被替换目录。

Agent重启会读取本地状态，再核对服务；不会自行重试业务任务。平台故障时既有任务遵守原租约超时策略，Agent不延长租约。监测失败标记failed而不是零负载；日志有界，超过64条时保留最新事件，不能作为完整审计历史；管理员操作审计仍保存在平台。

## 测试与生产边界

自动测试覆盖API权限/序号/会话/配置/排空，控制目录/恢复/探测超时，程序白名单/幂等/升级互斥，安装保留NFS单元、回滚和目标摘要门槛。真实Agent进程通过真实HTTP对接隔离API，systemctl由测试夹具替代以避免触碰生产机器；此测试不证明真实systemd/NFS故障或真实模型评测已通过。

生产迁移必须先排空一台节点，安装、验证Agent及原GPU，再逐台推进；只涉及已指定五节点，不操作.174。本变更不把代码测试当作生产节点已经升级的证据。

## 本次验收记录（2026-11-14）

- Agent/纳管/预检专项最终回归：56 passed，覆盖最后的权限隐藏、停止许可修订变化、升级后再次排空等修正。
- 后端完整回归：328 passed / 41 skipped。跳过项需要专用 PostgreSQL、真实 Docker/systemd、挂载命名空间等条件；未将跳过计为通过。
- 隔离 PostgreSQL：迁移到当前 head，补跑 Agent 配置/心跳/领取竞争、独占及共享 GPU 调度竞争、资源池成员调整竞争，4 passed；临时数据库容器已删除，未连接生产数据库。
- 前端隔离回归：40 passed / 2 skipped；另补跑 Agent 新旧节点展示、重新纳管和 SSH 进度恢复，4 passed。后者与前者存在重叠，不相加计数。
- 全量浏览器入口最初有5项失败：其中1项SSH完成状态丢失已修复并重测；其余4项依赖真实CS推理/执行环境或未提供的登录配置，未在静态测试站点完成，不能宣称全部浏览器端到端已通过。
- TypeScript/Vite 构建、OpenSpec 严格校验及 git diff whitespace 检查通过。
- 真实Agent集成使用真实子进程和HTTP，systemd通过夹具替代；NFS中断以受控探测/目录测试覆盖，未人为中断五节点生产NFS。

## 平台发布记录

2026-11-14：功能提交 `76dd6a6` 已部署本机18000，镜像 `mini-eval-api:node-agent-76dd6a6`。上线后真实登录、Agent状态API、机器详情页面、完整安装包摘要验证通过；原五节点及五张GPU在线，未解除纳管或替换生产Worker。

本次未对五节点安装Agent，因此页面显示“未安装”为真实状态。控制端网页SSH执行器也未作为本次发布启动；相关向导保留手动接入提示。生产节点滚动安装与真实systemd/NFS故障验收仍需单独记录。

发布快照、更新前数据库备份、页面截图和脱敏验收记录位于 `.runtime/releases/node-agent-76dd6a6/`（忽略入库）。未改变数据库schema；保留原镜像 `mini-eval-api:reenrollment-8ef72d3` 供回退。

## 真实节点验收（2026-11-15）

选择 `rtx3080-182`（192.168.8.182）作为首台验证节点：平台无持有执行，GPU无业务显存占用。R8000和.183存在其他显存占用，.157承担NFS服务，因此未在这些机器注入故障。验证期间关闭.182新任务准入，保留机器/GPU身份和资源池，不执行解除纳管。原程序、Worker配置及服务单元备份在该节点 `/opt/mini-eval-agent-canary-20261115/`，仅root可访问。

| 真实检查 | 结果 |
| --- | --- |
| 五节点SSH、Worker与共享盘基线 | 五台可达、在线；共享盘正常 |
| 安装Agent及独立升级器 | system级服务运行；控制状态在本地ext4，业务目录仍为NFS |
| 平台发布及升级 | 真实PostgreSQL写入成功；Worker升级到0.10.0 / 协议9，Agent 1.0.0；双方报告相同发布摘要 |
| 主机/GPU/本地磁盘/Docker探测 | 四类样本均为ok |
| 配置下发与回执 | 2秒心跳/5秒采样生效，最终恢复5秒/15秒；修订3已应用 |
| 排空停止及恢复 | 平台下发后真实Worker依次inactive、active；本次验证时无活动执行 |
| 重复Agent进程 | 第二进程因本地锁退出，原进程不变 |
| Agent重启 | Agent PID变化，Worker PID不变；配置修订保持并重新上报 |
| 旧修订/非法周期 | 分别返回409/400，不改变有效配置 |
| 升级启动失败及回滚 | 临时systemd ExecStartPre仅失败一次；平台记录FAILED/upgrade_failed；原目录标记恢复，Worker及Agent均重新运行 |
| 故障清理和正常重试 | 移除临时drop-in与标记；确认回滚后解除升级屏障，再次升级SUCCEEDED |
| 验收后恢复 | .182重新启用；五节点均online、无持有执行；原Worker配置和NFS服务单元逐字节未变 |

生产发现并修复两项问题：

1. `WorkingDirectory`不能使用ExecStart的整串引号规则。安装器改用路径转义，增加普通路径及包含空格、百分号路径的真实 `systemd-analyze verify` 回归测试。
2. 发布摘要64位，原审计目标字段仅36位，真实PostgreSQL拒绝写入。将审计目标改为`worker-releases`，完整摘要保存在`details.release_id`，不截断摘要、不变更数据库schema；补审计契约测试。

单独向旧版Worker复制Agent可能缺少`worker.host_environments`等依赖，不能当作完整安装。此次通过独立升级器安装完整发布包后恢复正常。常规纳管应使用完整安装器；手动部署Agent前应保证Worker/runner来自同一发布版本。

本次专项回归39 passed，包含Agent、安装/升级、独立升级器、纳管。真实故障演练使用相同发布包重新部署，通过启动失败和原目录标记验证回滚机制，并非跨不同业务版本的兼容性验证。未进行忙碌任务下的生产排空、主机重启、生产NFS断连、长时间浸泡或真实模型评测；这些不能由本次空闲节点验收代替。其余四节点保留原Worker，尚未安装Agent。

平台修复已部署至本机18000，镜像`mini-eval-api:agent-production-fix`。执行记录在本机 `.runtime/agent-production/`（忽略入库）；故障注入已清理，保留备份供恢复。

## 五节点推广（2026-11-15）

已在用户指定五节点安装Agent与独立升级器，Worker全部升级至协议9。真实忙碌排空、Agent网络隔离与恢复、损坏状态恢复、Worker崩溃恢复、每节点三次Agent重启和SDK排队任务验收见[五节点验收记录](node-agent-validation.md)。本轮还修复配置/事件写盘失败后内存状态提前生效的问题，修复已部署到五台节点；平台镜像为`mini-eval-api:agent-fleet-recovery`。
