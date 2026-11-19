> 2026-11-19 用户撤销此扩展：仅需按评测向导展示既有配置及映射后的命令。此方案不再实施；未完成代码已撤回，没有快照表迁移或 Worker 上报部署。后续工作归入 batch-run-configuration。以下保留原讨论，不作为待实施要求。

## Context
见 proposal.md。已核对：worker/host_backend.py 的 launch.json 保存平台 Python/Runner 命令；业务参数在 runner/cs_entry.py、service_entry.py、host_episode.py、command_sdk.py 才完成 bind_values/bind_platform 与 for_template。process_log_run.run 负责新日志模式的 Popen，但旧日志分支直接 subprocess.call/Popen，因此不能只在日志采集中记录。task_input.build 构建 task/business 命名空间；host_episode.episode_input 将 episodes 改成 1，seed 改为当前回合 seed。API jobs detail 有 id、episodes、client_spec 和 attempts；当前前端展示的是静态快照，不能证明节点实际 argv。

## Goals / Non-Goals
Goals：跨宿主机/容器、客户端/服务端、整任务/逐回合保存真实创建参数；快照不改变协议结果及退出码语义；离线可查、同名多实例不可串记录。
Non-Goals：不自动重跑、不导出完整 shell 环境、不把 exec 前的快照当作业务已就绪、不承诺监控业务自行派生的所有子进程、不借本特性更改权限和资源隔离。实际命令必须按 argv 数组展示，供阅读的 shell 文本明确非可直接重跑命令。

## Decisions
1. 采集边界：新增共用启动记录器，在各入口完成全部映射、业务账号环境与 cwd 解析后、创建业务进程前调用；新旧日志路径复用此边界。SDK 原生单进程记录 business 一份，逐回合子进程记录 episode；SDK-command 的平台包装进程不能冒充业务进程。服务记录 service；脚本内部 exec 的后续解释器不推断，记录 argv[0] 及当前可确认的可执行文件路径。选择实际创建边界而非 Worker launch.json，是为包含最终 taskname、参数和回合改写。
2. 身份与状态：execution_id + scope(business/episode/service) + episode_id + launch_id(UUID)；节点/槽位/服务实例由 API 根据 Run/Job 关联，不信任 Runner 提交的归属。不可变 prepared 载荷另加可递进的 spawn 状态（spawned/spawn_failed）；prepared 只表示即将创建，spawned 表示创建调用成功，不表示服务就绪或业务成功。跨崩溃无法确认时保持 prepared/unknown，不伪造成功。不自动增加任何业务重试；已有重试必须使用新的执行尝试或明确新 launch_id。
3. 数据：schema_version、UTC 采集时间、execution_mode、scope、episode、argv、cwd、UID/GID/可解析账号名、实际可确认的 executable、环境来源、允许的环境子集、脱敏/截断标志、Runner/Worker 版本。采集时系统账号不可解析仍保留数字 UID/GID。节点时钟与平台接收时间分开显示。环境只收录设备可见性、平台路径/回合信息及 PATH、LD_LIBRARY_PATH、ASCEND_HOME_PATH 等明确允许诊断的键；不复制全部登录环境、API/SSH 凭据。argv 中常见敏感选项、URL 凭据必须脱敏；自由位置参数无法凭名称识别秘密，说明这一边界且沿用业务输入不得携带凭据的规则。原始秘密不得先写本地再脱敏上报。
4. 持久化：专用 ExecutionLaunchSnapshot 表和 Alembic 新迁移，唯一键 execution_id/launch_id，分页索引 run_id/scope/episode_id；payload JSON 与 SHA256，创建阶段和错误码独立字段。不要塞入 result_json（会被任务结果覆盖），不要借用会淘汰的日志文本或有独立业务产物配额的 RunArtifact。Run 删除时纳入现有批次删除事务和外键顺序；保留随执行记录生命周期，已上报数据不依赖节点在线。
5. 传输：Runner 原子写执行目录下独立受限 outbox，使用只属于当前执行的预配置目录，Worker 扫描上报。模板/程序输出不能通过 stdout 注入可信记录。对本地文件执行限长、no-follow/类型/归属验证，Worker 绑定自己的执行身份；业务账号仍可能修改自己可写的内容，快照为诊断证据，不宣称不可抵赖审计。Worker 独立有界批处理，响应丢失幂等重传，记录确认进度，不能堵塞心跳或因为 API 离线改变进程退出码。终态前尝试刷新，失败进入持久待传队列；启动过的执行在终态后可有限期补传，仍校验原 Worker/Run 归属，节点撤销后禁止写入。
6. 容量：单快照最多 64 KiB，每个执行最多 10002 个启动身份、总元数据 16 MiB；超限保留身份与明确的内容省略状态，界面不得把缺失当作完整 argv。列表默认每页 50、上限 100，详情按需加载。边界计数和 Worker outbox 清理须与重试确认一致；清理未上传数据时先留下缺失原因，诊断失败不得导致业务失败或额外启动。
7. 协商与兼容：新增 execution_launch_snapshot_v1 能力，API 下发后才启用。旧 Worker/Runner/镜像显示未记录/不支持，不从模板推算实值。版本升级顺序为迁移→API/前端→排空并更新节点 Worker/Runner；不因增加能力强制停止已有任务。
8. UI：运行配置保持“提交快照”，新增“实际启动快照”入口，先选择执行尝试，再按 business/episode/service 分组；详情、回合行及服务实例行均可进入同一视图。显示节点/卡、账号、cwd、最终 argv、脱敏环境及阶段，下载同一脱敏 JSON。无启动、无记录、待上报、记录失败、内容省略分别说明。对每回合启动的静态 task.episodes/task.seed 增加总量/基础值提示，业务实际值只能从相应回合快照读取。已保存 job id 不再标为运行时未知。

## Risks / Trade-offs
- 保存最终参数会扩大数据暴露面 → 采集端脱敏、API 再校验、沿用任务所有者/管理员授权，错误日志不打印原始载荷。
- 诊断磁盘或 API 故障 → 业务继续，快照状态明确失败/待上传；不会为补快照重启程序。
- 上万回合增加数据库量 → 仅启动边界写入，限制大小、分页、批次删除回归；不走高频 heartbeat 大载荷。
- 容器 UID 不是宿主机 UID → 明确身份作用域，容器与宿主机分别标注。
- 实际入口为 shell 脚本或 Python SDK → 只记录平台启动的进程，不把后续内部 exec 或每个 SDK 回合伪造为独立启动。

## Migration Plan
实施时新增数据库迁移及升级/回滚测试，不能声称当前无需迁移。部署前只读检查活动任务；先备份并迁移，再更新 API/前端，最后排空升级 Worker/Runner，旧 Worker 继续原行为。旧式镜像依照既有平台 Runner 注入/镜像更新流程验证。回滚关闭新能力，保留表中历史数据，避免自动降级删除证据。真实 GPU/NPU 宿主机和容器验证均需实际执行及记录；不可将 mock 浏览器或普通 echo 当真实模型验收。
