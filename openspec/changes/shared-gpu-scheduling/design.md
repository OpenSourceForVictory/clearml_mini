# Design

## Context

现有 `core.py` 的活动 Run/Slot 唯一索引、`execution.py` 注册型号检查与 `worker/client.py` 单执行 journal 共同约束单卡串行。调度已有 PostgreSQL advisory transaction lock、用户轮询、全局/节点额度、租约和清理确认。沿用这些可靠性机制。

参考 ClearML Agent 的 [动态 GPU 可用额度计算](https://github.com/clearml/clearml-agent/blob/a1af51f25b999669b0b56a23c6c427c1b717c2f7/clearml_agent/commands/worker.py#L1918) 及 pending reservation，而不是把遥测利用率直接当可分配百分比。官方 fractional GPU 镜像不作为依赖。

## Goals / Non-Goals

**Goals:** 实现可验证的同卡共享、异构资源池、独立恢复与透明资源账本；保留单卡单进程所有权锁。

**Non-Goals:** 不保证 CUDA 显存硬上限，不引入 MPS/MIG，不承诺算力百分比或基于 TFLOPS 的吞吐预测。实际评测样本尚不足，历史性能预测留给后续独立变更。

## Decisions

1. 物理 Pool 的旧 gpu_model 统一为 `*`，`virtual` 仍用于模拟池。Slot 保存真实 gpu_model、vram_mb、protocol_version。任务增加 gpu_mode（exclusive 默认/shared）、requested_vram_gb（独占可为 0）、required_gpu_model。Pool 增加 allow_shared、max_tasks_per_gpu（默认 1）、vram_reserve_gb（默认 2）、max_gpu_utilization（默认 90）。管理员可更新池及节点额度；已有执行采用预留快照，不因修改配置改变账本。空闲槽位可通过重新注册迁移到新池，有活动或未清理执行时拒绝迁移。
2. Run 保存 allocation_index、reserved_vram_mb、exclusive、reserved_cpu、reserved_ram_gb。唯一索引替换为 slot+allocation_index，条件覆盖活动或 cleanup_confirmed=false。独占与共享冲突、显存总和和节点限制在现有事务锁中校验。旧独占执行回填索引 0 和 CPU/RAM 快照。
3. 每次调度按用户轮询，候选卡按利用率、活动数量、稳定设备名排序。共享要求协议 v2 和 15 秒内遥测；预算同时满足 total-memory minus safety minus reserved 及 free-memory minus safety minus 尚未启动的额度。RUNNING 在容器确认启动后上报；STARTING/RESERVED 都视为尚未启动，宁可保守等待。取消中且尚未启动的执行也计入启动预算。利用率超阈值只停止新共享准入，不抢占。独占旧协议可继续无遥测运行；声明显存需求的新任务要求新鲜遥测。
4. 新 claim 接受已知 execution IDs，返回未被本进程处理的一个活动/预留执行；旧空请求保持原重领行为。任务使用 Run 资源快照。每任务独立 Worker 对象/线程及 journal 文件，单 GPU 监督进程持有槽位锁。主循环维持设备遥测与领取；启动/创建串行化，任务运行并行。旧 journal 转存后恢复，服务器可补回丢失领取响应的执行。无 journal 的已启动执行采取保守失败而不重新创建。
5. 设备心跳只更新设备可用性；执行心跳单独续租，启动前续租不标记 RUNNING。每次有显存要求的容器创建前重新查询 nvidia-smi，发现容量不足安全失败并说明原因。采集失败使遥测失效，不把失败伪装为空闲。
6. 前端显示调度预留与实际显存，创建池默认异构物理池；提供共享策略和任务需求。兼容旧 API 默认值与旧单任务 Worker 协议。

## Risks / Trade-offs

- [任务超出声明显存] → 调度额度不是硬限制，文档明确；保留安全余量、启动检查、默认独占。
- [两次遥测间外部进程占用] → 启动前复核；仍不能完全消除运行中 OOM，不宣称硬隔离。
- [配置降低导致超额] → 已有执行不抢占，快照保留，新任务等待额度满足。
- [共享 Worker 失联] → 各任务租约看门狗停止对应容器，服务端隔离整卡直到所有遗留执行确认清理。

## Migration Plan

先隔离 PostgreSQL 迁移与并发验证，再验证 Worker 多执行恢复和前端构建。部署前等待活动任务清空并暂停 Worker/API，备份数据库；迁移数据库、更新 API 和两台 Worker、启动后检查遥测与队列。新池默认独占，可在空闲时将两台卡重新注册到同一池。开启共享需同步提升节点并发上限。回滚须先排空共享执行，再回退数据库与代码；不在活动共享执行期间重建旧唯一索引。R8000/4090 只验证双机各单卡，不冒充多卡主机验收。
