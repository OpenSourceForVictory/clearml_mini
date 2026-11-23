# Design

## Context

依据当前 `089d7fe`：Run 已有 enqueued_at、started_at、finished_at；领取时将 enqueued_at 设为平台 now 并进入 STARTING；首次 started 心跳才设置 started_at。后者不是精确进程出生时刻，不能用它作为所有失败任务的唯一起点。状态转换写 Event，重试新增 Run，不保留 Job 上的上一尝试起止值。

`main.detail` 未返回每次尝试起止字段；monitoring 与 EpisodeList 未展示耗时。sdk.run 的成功事件含单调计时 runtime，ERROR 不含；episode_contract 严格拒绝额外字段，不能直接往旧协议塞新字段。EpisodeRecord 的 started/result JSON 可保存可选计时，updated_at 不能替代准确结束时间。独立日志是可选且可降级的，不能作为计时唯一来源。

`result_views.per_episode_process` 已将 sdk-command 与 host/per_episode 识别为独立回合进程。沿用此分类，避免“协议有回合”与“每回合启动进程”再次混淆。相关端到端调用包括 runner/sdk.py、runner/host_episode.py、runner/command_sdk.py、runner/cs_entry.py 及 Worker 的事件转发。

## Goals / Non-Goals

**Goals:** 三层时间语义一致；普通命令和失败也有整次时间；独立回合自动计时；使用现有持久化及转发；重试、旧记录、混合节点版本可解释；界面与导出复用投影。

**Non-Goals:** 不做资源计费或 GPU 核时；不根据日志推断时刻；不更改执行时限、续租、调度顺序、退出码或协议结果；不新增精确的进程级整任务监控服务；不把服务常驻时长纳入客户端批次完成条件。本次仅规划，未实现。按用户最新决定，批次不增加累计执行耗时或区间并集统计，只保留整体历时。

## Decisions

### 1. 三层口径

| 层级/字段 | 起止口径 | 展示 |
| --- | --- | --- |
| 批次历时 | batch.created_at → 当前最新尝试全部终态的最大 finished_at；未全部结束则到 as_of | 含排队、暂停和补跑间隔；不是各任务求和 |
| 尝试等待 | 本次 PENDING 事件时间 → enqueued_at；未领取时到终态或 as_of | 包含资源等待及服务就绪等待 |
| 任务执行耗时 | enqueued_at → finished_at；未终态到 as_of | 含准备/启动及结果处理，不包含排队和终态后清理 |
| 独立回合执行耗时 | Runner 开始该回合执行 → 该回合返回/抛错 | 含该回合输入准备、启动及结果解析，monotonic 差值 |

整任务执行耗时不是业务核心计算耗时。详情可保留已上报 runtime，名称明确为“业务上报耗时”，不替换主指标。任务耗时通常不等于回合之和，因为有初始化、回合间隙、清理及结果处理；不人为凑齐。

未领取即取消：执行状态 not_started、秒数 null，等待可有值。领取后启动失败：任务执行有值，不代表业务成功启动。终态后资源仍清理：执行计时停止，清理状态单独保留。

等待起点按 Run 的首个 PENDING Event，而非会被重试覆盖的 Job.started_at 或首次 Job.created_at。事件缺失时首次尝试允许 job.created_at 并标记 fallback；重试缺失起点则返回 unknown。批次只计算客户端 Job 最新尝试，排除派生服务 Job；无有效任务/终态缺少 finished_at 时返回 unknown，而不是退回当前时刻永久增长。

### 2. 统一投影，避免额外写库

新增 backend/app/execution_timing.py，共享纯函数与批量查询。任务详情、monitor、episodes 和导出调用同一实现。复用 Run 时间及 Event 时间，不因前端计时每秒更新数据库，不新增整任务 timing 表。

建议响应结构（秒数为有限非负数或 null）：

```json
{
  "timing": {
    "as_of": "UTC RFC3339",
    "mode": "whole_task | per_episode",
    "execution": {"state":"running | finished | not_started | unknown", "started_at":null,"finished_at":null,"seconds":null,"source":"platform_lifecycle","quality":"recorded | fallback | unknown"},
    "waiting": {"state":"running | finished | unknown","seconds":null}
  }
}
```

回合 timing 添加 duration_seconds、phase（running/finished/interrupted/not_started/unknown）、source（runner_monotonic/legacy_reported）、起止 UTC 及质量。批次使用 elapsed 字段而非 execution，防止误解。服务端统一取一次 as_of，分页结果不能各行使用不同当前时点。

当前任务行默认最新尝试；历史选择器对应 run_id 查询独立时间。分页批量取 Event 的最早 PENDING 时间，不逐行查询；已核对 job_events 的 run_id/new_status 无覆盖索引。增加 (run_id, new_status, created_at) 复合索引迁移，针对按 run_id 批量取 PENDING 起点的查询；用 EXPLAIN 和大批次用例验证，避免每次刷新扫描历史事件。

### 3. 回合自动采集，隔离协议结果

增加轻量 runner/episode_timing.py，在 sdk.run 调用 run_episode 的边界计时，覆盖成功和异常。STARTED 记录 UTC 起点；COMPLETED/ERROR 带相同起点、UTC 终点和 monotonic elapsed。只对 per_episode_process 生效，native SDK 单进程多个函数回合不因此增加独立进程耗时。

扩展 episode_contract 的可选 `timing` 对象（schema=1），不改原 runtime 的要求或业务含义；在 ERROR 事件中也能携带完整 timing。限制对象键、UTC 格式、有限非负 duration、时间长度和事件大小；不要求 UTC 终点差等于 monotonic 差。墙钟倒退时记录 clock_adjusted，不产生负耗时。

本地写入及现有 Worker 事件转发仍用每执行独立 episodes.jsonl，不另做 socket/配额通道，不依赖 process logs 开关。Runner 托管的外部 direct 子进程所输出的嵌套回合事件维持既有过滤，主计时由外层生成；业务自己的 runtime 不覆盖计时。调用 emit 出错仍走既有结果交付失败策略，不因时间通道重启业务。

计时元数据属于经过同一权限验证的诊断数据，不是计费可信凭证。相同完整事件重放幂等；不能用新 timing 覆盖旧终态事件来“补齐”。完成事件缺失的回合显示 interrupted/unknown，强杀不能保证执行 finally，不承诺精确结束时间。

### 4. 运行中显示和时钟

任务/批次使用 API as_of 与平台生命周期时间。前端以返回秒数加 performance.now 的短时增量展示，下一次轮询校准，不用浏览器 Date.now 减服务器时间；请求失败超过两轮后冻结并提示“更新中断”。刷新页面重新读 API，服务端终态停止计时。

回合 running 没有最终 monotonic duration；以平台接收 STARTED 的时间作“已运行约”参考，明确受上传延迟影响。为避免新增时间列，接收时间写在 started JSON 的 `_platform_received_at` 私有键，其余原事件字段保持原形；兼容 helper 去除该私有键后再做幂等比较和 API 原事件输出，Worker 输入校验禁止提供私有字段。重复 STARTED 不改接收起点。不得把 EpisodeRecord.updated_at 当起点（结果到达时会更新）。历史无接收时间只显示运行中、耗时待上报。

服务端已判任务超时/取消而回合没有最终计时，停止前端增长并显示“已中断，完整耗时未记录”。晚到合法计时只沿现有活跃/终态事件接收规则处理，不为计时放宽权限或重开终态。

### 5. 页面安排

- 批次页顶部新增“批次历时”，附“含排队”说明；批次表可显示同一值，非累加 CPU/GPU 工时。
- 任务行新增一列“执行耗时”，小字显示等待时间；详细起止日期放到“详情 / 重试”的执行尝试中。运行显示“已执行 3分12秒”，终态固定显示。宽度不足横向滚动，不把全部日期塞入主表。
- 逐回合进程表新增“回合耗时”。未启动显示“未启动”，历史缺失“未记录”，强杀缺结束记录“已中断”。非逐回合模式不渲染该列，原协议回合结果保留。
- 同一格式器：小于 1 秒保留毫秒，随后秒/分秒/时分秒；日期详情按用户本地时区，导出保留 UTC 和原始秒数。null 不能显示 0，0 的真实记录合法。
- 程序退出和业务结果仍独立，不用耗时判断成功，也不让计时状态取代任务生命周期。

### 6. 方案取舍

仅展示业务 runtime 最省代码，但普通命令、ERROR 和旧记录不完整，故保留为补充字段。纯用服务端接收事件时间统计回合会包含上传/日志延迟，故最终采用节点单调计时。为精确到业务子进程出生/退出而新增全局 supervisor 时间服务，超出用户需求且增加恢复复杂度，本版不采用；界面明确显示执行生命周期/回合调用耗时。

## Risks / Trade-offs

- [任务结束时间包含上报延迟] → 明确生命周期口径；不称为纯业务进程运行时间。
- [原始事件 JSON 已被多处直接比较] → 新平台接收元数据采用兼容读取/比较 helper，同步所有 episode 查询及导出；新增重复 STARTED、重复结果、旧事件回放测试。
- [旧 Worker 校验拒绝 timing] → API/Worker/Runner 能力协商后才发送新字段，不能只升级下载 SDK。
- [运行中回合估计有上传延迟] → “约”及来源提示，最终以 Runner duration 替换；不在两套节点墙钟间求差。
- [SIGKILL/断电没有完整终点] → 缺失可见，禁止估算成精确值。
- [大批次逐行查询及定时器压力] → 分页批量聚合、页面共用计时 tick、继承现有轮询，不新增每行网络请求。

## Migration Plan

1. 先发布 API 可选字段读取/校验与时间投影，再发布前端。旧记录 JSON 保持可读，不回填虚构时间；先执行 job_events 复合索引迁移。该迁移不改数据列；生产 PostgreSQL 使用支持并发创建的独立迁移事务策略，SQLite 测试走普通索引 DDL，并验证建索引失败后的重试。
2. 增加 `episode_timing_v1` 能力，claim 协商后保存在执行快照；新 Worker/Runner 仅在协商开启时生成/转发 timing。旧任务快照不切换协议。下载 SDK、Worker 打包及容器 Runner 挂载/镜像路径一并验证；遗留自带 Runner 镜像需业务方更新。
3. 整次任务/批次时间无需升级旧节点；精确逐回合新增计时需要升级 Worker/实际 Runner，旧节点正常运行并展示缺失/业务上报来源。
4. 回滚先停止新执行的计时协商，排空或结束已经启用的执行后回退 Worker/API；不得让旧严格校验器接收带 timing 的在途事件。前端可独立回退，索引可保留，不影响旧版本；需要降级时仅删除新增索引，不删除 JSON 历史数据。

## Verification

重点验证：整任务普通命令/直接协议/native SDK；host per_episode 的 command/direct/sdk-command；Docker sdk-command；每种正常/非零/缺 final/异常/取消/超时；早停与继续；未开始/历史无数据；重试后回放不串；节点及浏览器时钟偏差；API断连补报/事件幂等；日志启停；大批次和窄屏。纯时钟与聚合用确定性测试，进程边界用可运行子进程，真机验收保存可在界面查看的 host/Docker 样例，不冒充模型评测。
