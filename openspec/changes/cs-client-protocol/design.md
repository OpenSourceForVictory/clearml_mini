# Design

## Context

当前 `worker/client.py` 仅接受固定 Runner 镜像，容器 `--network none`；API Submit 使用 model_ref，Run 存日志和固定三项指标但没有产物。用户明确第一阶段只管理 CS 仿真客户端，本次先实现协议，以测试服务验收。

## Goals / Non-Goals

**Goals:** 让不同客户端通过同一文件和进程契约接入，保持调度/租约/恢复机制；不把客户端协议耦合到单一模型服务。

**Non-Goals:** 不托管推理服务和 checkpoint，不实现完整 RoboTwin，不实现通用 HTTP→WebSocket 转换、大文件对象存储或硬网络出站隔离。外部服务认证先由客户端自身配置机制适配，表单和 endpoint 不接收明文凭据；集中密钥管理另行设计。

## Decisions

1. 新增 ClientTemplate（name/version 唯一、image、command argv、protocols、enabled）和 Job.client_spec JSON。CS 提交 benchmark=cs-client，接收 client_template_id、endpoint、task_name、seed、request_timeout_seconds、timeout_seconds、parameters。校验后写不可变快照，model_ref 自动使用模板名/版本。模板创建后只允许停用/启用，版本变更创建新记录。普通任务默认值保持兼容。
2. Slot 新增 client_images JSON，注册 v3 接收 Worker 配置 `client_images` 镜像列表；调度要求 v3 且快照镜像在声明列表中。Worker 再检查白名单。镜像须预先分发到节点且包含 Python 与平台 `runner` 目录；生产建议 digest 固定。模板只能由可信管理员登记。
3. CS 任务 `--network bridge`，不开放入站端口、不使用 host/privileged；仅挂载第 14 条规定的管理员工作目录；渲染保留 graphics 能力。输入 JSON 经 docker cp 写入容器 `/tmp/mini-eval-job.json`，无 shell 插值。入口 `runner.cs_entry` 设置 MINI_EVAL_INPUT/OUTPUT_DIR，做有界 TCP/TLS 端点连接预检并通过 `runner.supervise` 管理客户端子进程。服务地址支持 http/https/ws/wss；客户端负责自身应用层协议及请求超时。总超时同时进入服务端 reconciliation 与容器监督器。
4. 服务地址是供 Worker 容器访问的地址，拒绝回环、未指定地址、链路本地及元数据地址的 IP 字面量；允许内网机器地址/DNS。预检对实际 DNS 结果再校验。只承诺连接预检，不把 bridge 宣称成严格端点白名单。失败信息不输出服务响应正文或 URL 中凭据。
5. 固定输出目录 `/tmp/mini-eval-output`。Worker 在容器退出后以流式 tar 读取 docker cp 输出，检查普通文件、路径和文件/总量上限，不解压到宿主机路径；以 base64 有界 JSON 上传。RunArtifact 存二进制、名称、SHA256 和字节数，使用 run_id/name 唯一约束，保持 API+DB 轻量部署和现有备份覆盖。最多 8×8 MiB、总量 32 MiB；超额明确失败，视频限制在短片范围。
6. 产物上传发生在完成确认前，每文件续租且幂等。容器缺少输出目录可视为空产物，日志和指标契约仍有效。数据库 metadata 查询延迟加载二进制，下载按任务权限返回附件；不做 HTML 内联预览。
7. 新增 EvaluationBatch 和 Job.batch_id/case_id。批次提交清单在单事务中校验/入库，复用独立 Job/Run 的调度、租约、重试、产物协议，避免一个大客户端黑盒自行遍历任务而平台不知情。每项调度粒度为仿真任务的一组回合，不承诺回合内断点。批次进度从子任务状态实时聚合；失败项使用既有重试接口，成功项不重跑。批次幂等键按用户唯一，批次完整入库，使用独立的批次积压额度（默认每用户 10000 个未完成子任务），不受单任务提交的默认 20 项排队额度限制。
8. 示例使用简单离散状态客户端与独立 HTTP 测试服务，无 GPU/仿真资产依赖，验证 observation→action 交互及报告回收。明确不是 RoboTwin 模型评测。UI 提供模板管理、CS 输入、配置快照和各尝试产物列表/下载。

## Risks / Trade-offs

- [自定义镜像不符合契约] → 管理员先本地验证镜像；平台明确报告启动失败，配套 Dockerfile 和文档提供可复制范例。
- [客户端忽略请求超时] → SDK 示例遵守该值，容器及服务端始终执行总超时，不能替任意客户端保证每个网络库的请求行为。
- [视频增加数据库体积] → 明确有界小文件协议和限额；大产物存储单独扩展。
- [外部服务负载/速率] → 仍由用户选择客户端并发，不承诺推理服务端资源调度。

## Migration Plan

增加模板表、产物表及可空/默认字段，旧任务无需回填。先在隔离 PostgreSQL/API/Docker 验证正常交互、服务不可达、总超时、取消恢复及产物鉴权，再备份升级服务端和 Worker。部署示例模板但不常驻开放测试推理服务；真实地址由用户提供。回滚前停止 CS 执行并备份产物，恢复升级前数据库/镜像；旧 Worker 可继续执行旧类型任务。

## 用户确认的框架扩展

9. EvaluationSuite 保存 owner/name/version/template_id/cases，版本内容不可变。批次是运行实例，保存 suite_id 与配置快照；修改集合需新建版本。批次 max_concurrent 默认 1，预留和清理中执行均占并发额度。调度按用户轮询，再按各用户的集合轮询；单任务视为独立组。共享利用率仍为池级准入参考，不宣称算力硬配额。
10. SDK 提供 EvaluationClient.setup/run_episode/teardown 和 EpisodeResult。回合编号从 1 开始，种子按 (task_seed + episode_index - 1) mod 2^31 生成，计划在提交时由这些不可变字段确定。SDK 接管循环，上报带固定前缀的 JSON 事件；Worker 将事件逐条可靠转发到鉴权 API，容器不获得平台凭据。服务器按 run_id/episode_id 保存开始/完成/异常，重复事件幂等、冲突拒绝。回合目标失败是有效完成，执行异常不计已完成。尝试之间分开统计，不承诺回合内或跨尝试续跑。
11. SDK 模板显式声明 episode_reporting，要求所有回合上报完整才允许成功收尾。历史命令客户端保持可用但界面明确未提供回合进度。指标类型限定有界有限数值，聚合 success_count/completed_count；自定义指标保留逐回合值，不盲目平均。失败或中断保留已上报结果。GET 回合结果分页且按任务所有权鉴权。
12. 基础镜像包含 runner/SDK/监督入口，用户派生镜像安装依赖和仿真资产。提供可运行的单回合 HTTP 示例。代码构建须隔离于 API 进程，由独立构建执行器处理受控代码包、依赖与入口，记录不可变源码摘要、构建日志、镜像标识；不把任意构建操作放入 API 容器。

13. 用户确认暂不提供专用构建机器：部署代码队列，交付手动构建执行器，不在评测主机自动执行任意用户构建。
14. Worker 增加 workspace_root（绝对路径，默认配置旁专用目录），布局 state/、assets/、cache/、runs/<execution_id>/work/ 与 output/。公共目录只读，work/output 独立可写；容器输入快照保存在执行父目录不挂载。环境变量 MINI_EVAL_ASSETS_DIR/CACHE_DIR/WORK_DIR 指向容器路径，基础环境由版本化镜像提供。保留旧 state_path 兼容恢复。SDK 同时将事件追加到 work/episodes.jsonl，Worker 从专用事件文件读取，避免 Docker 日志轮转丢失回合结果；拒绝事件文件链接。数据保留，管理员按磁盘容量和执行终态清理，不自动删除。
15. 磁盘低水位保护：Worker 上报 workspace_root 所在文件系统可用 MiB 和管理员配置阈值（默认 2 GiB）；调度器在低于阈值或磁盘遥测过期时不新增任务。启动前再次检查，异常明确失败。此保护不是每任务磁盘容量预留或硬配额，Docker 镜像分区需另行监测；运行中写入错误按执行异常处理，不自动删除任何数据。
