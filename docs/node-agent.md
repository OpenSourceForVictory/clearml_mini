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
