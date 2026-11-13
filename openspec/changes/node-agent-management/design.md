# Design

## Context
worker/client.py 当前按 GPU 实例部署，已有执行 journal、租约 watchdog、Docker/host 后端、目录配置与回执。worker/updater.py 独立于被更新目录，具备排空和回滚。backend/app/administration.py 用 GPU 心跳推断节点在线。完整节点安装和网页 SSH 纳管正在另一变更中开发；不得将它们的未验证状态计为本变更验收。

## Goals / Non-Goals
Goals: 建立真正节点级管理平面，模块独立测试，控制与业务故障隔离，兼容现有五节点，保留一机多卡共享调度。
Non-Goals: 本次不替换现有调度器、不引入推理服务池、不改写评测业务、不把所有模块拆成网络微服务。逐 GPU Worker 是过渡执行后端；转为按执行分配多卡进程是后续演进，不能宣称本变更已实现。

## Decisions
1. Agent 主进程模块为 transport、state、monitor、programs、coordinator；运行环境、工作区、SDK 和任务恢复复用现有 worker/runner 模块。升级器独立，程序管理只有一个写入方；升级期间 Agent 不启动 Worker。
2. 首版使用现有节点凭据访问新增 agent/* API；业务程序仍只有执行身份。管理员 API 单独授权。节点注册获得服务器会话，心跳包含会话和单调序号。进程本地单实例锁；会话被替换后旧 Agent 停止控制。
3. 节点 connection.agent 持久化受限管理记录，避免重复资源账本；写入在现有调度锁内合并，不覆盖 enrollment。保存期望配置、配置 revision、会话、最后上报、最近事件；事件和指标严格限制大小与类型，不持久化凭据。
4. Agent 心跳由独立快速循环处理，monitor 在后台有界采样，外部工具统一超时；磁盘监测仅本地控制目录，NFS检查复用 Worker工作区模块，避免 stat 阻塞主循环。采样失效不能等同零利用率。
5. 程序清单在安装时本地固定，只支持 mini-eval-worker 单元；API 只传 running/stopped 意图，不能传 shell/unit/path。停止先原子关闭新任务准入、释放未领取预留、等待持有执行清零。现有任务续租/清理接口不受 Agent 准入影响。
6. 配置一期包含采样/心跳周期及 Worker 期望状态；工作目录、资产、镜像、宿主机环境保留已有各自 revision/ack，界面说明清楚，避免两个配置源。程序控制异常回执失败，不让 applied revision 冒充成功。
7. Agent 程序与 Worker 程序分开存放，控制状态和日志只用节点本地目录，禁止 NFS/CIFS 类型控制目录。临时安装、升级、systemd用户环境均可独立测试。升级包允许 node_agent 文件；独立升级器验证 Agent 和 Worker 摘要，旧配置无 Agent 时保持旧校验。
8. 测试分层：纯函数契约/状态/重试/容量；API鉴权/并发修订/会话隔离；真实 Agent 对隔离 API；受控 systemd命令夹具和真实进程；前端用户流程；调度/服务/安装/升级/SDK全回归。模拟 GPU 不称真实模型评测。

## Risks / Trade-offs
- 旧 Worker 和 Agent 都操作服务 → Worker不管理自身服务，Agent在升级状态停止协调，独立升级器独占升级锁。
- 节点断网/迟到停止 → 停止许可绑定配置修订和会话，执行前重新取许可；恢复运行必须等待前次停止回执，不能直接恢复准入。
- 控制状态损坏 → 拒绝启动并保留文件，不能忽略恢复记录继续操作。
- 日志泄密 → 只记录结构化固定事件码，不回传原始命令输出和配置凭据。
- 测试未覆盖真实 systemd/NFS → 分开报告隔离集成与生产验收，不把 mock当生产通过。

## Migration Plan
先上线向后兼容 API/界面，再安装隔离节点验收，然后逐节点排空部署。保留原单位、目录、GPU身份。回滚先停止 Agent 控制、保持节点关闭，恢复旧程序，验证后管理员恢复准入；不自动改变资源池。生产只涉及指定五节点，不涉及 .174。

## References
- https://clear.ml/docs/latest/docs/fundamentals/agents_and_queues/ ：执行环境准备、任务执行。
- https://developer.hashicorp.com/nomad/docs/architecture ：中心调度和节点客户端分工。
- https://kubernetes.io/docs/concepts/architecture/nodes/ ：区分节点心跳和就绪条件。
