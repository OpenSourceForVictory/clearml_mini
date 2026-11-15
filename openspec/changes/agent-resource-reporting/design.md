# Design
## Context
Agent 已有独立探测线程，但事件确认同步 fsync；Worker 在心跳中同步探测 NFS、GPU，并执行配置持久化。调度当前仅使用配置 CPU/内存容量。
## Goals / Non-Goals
目标：控制心跳读取有界缓存，统一整机资源来源，探测失败关闭新任务准入，保留执行租约独立性。
本次不迁移每卡 Worker 为单节点服务，不改变任务资源申请格式和既有并发策略；这些后续调度变更以本接口为基础，不宣称本轮完成。
## Decisions
- Agent 注册声明 resource_protocol=1；旧 Agent 默认为0。新版采集 GPU UUID、总/空闲显存、利用率，主机核数/总内存/可用内存和非阻塞CPU时间差分。
- CPU/GPU 动态采样至多间隔5秒；心跳只读快照。每个探测最多一个后台线程，卡死不增加线程，过期或失败不视为空闲。
- Agent事件确认移入串行控制线程，无变化不写盘，配置确认依然以持久化成功为准。
- Worker存储、环境、镜像和GPU兼容采集由单一后台控制器发布缓存；超时返回未就绪，主心跳不直接访问NFS或执行探测命令。控制器正在应用配置时禁止领取新任务，避免执行目录切换竞争。
- 后端仅接受当前 Agent 会话的资源样本，以UUID校验GPU索引，保留源采样时间。协商成功后忽略Worker资源覆盖。CPU/内存预留仍按配置容量，同时核对探测容量和可用内存；总容量扣除所有预留，实时可用内存额外扣除未启动预留，避免重复承诺和重复扣减已运行任务。
- API网络请求设置总等待期限及单通道最多一个在途请求，网络失效不产生无限线程。
## Risks / Trade-offs
- NFS不可中断内核等待 → 每类工作只有一个在途线程，主心跳继续但存储关闭准入；不能承诺杀死D状态线程。
- 异构版本 → API先升级，Agent再升级，Worker最后升级；旧 Agent 保持原采集路径。
- 显存预算不是硬隔离 → 任务启动仍复核资源，运行异常依旧通过租约和清理确认恢复。
## Migration Plan
运行完整回归后按API、Agent、Worker顺序升级，先单节点验证再推广。回滚Agent需重新注册旧协议以恢复Worker资源来源；不得恢复过期快照。保留原执行日志、服务配置和资源预留。

## Heterogeneous machine extension
用户补充需要GPU与昇腾NPU机器。节点快照新增设备类型、厂商、设备标识、内存容量/可用量、利用率、状态、执行就绪字段；未知值为null，不能填0伪装为空闲。
NVIDIA采样转换为统一设备清单。昇腾以独立适配器采集npu-smi芯片映射、HBM容量、AI Core利用率和健康；在用户提供的8卡910B3/npu-smi25.5.1上只读验证，其他格式失败关闭。执行适配未完成，所以execution_ready=false，不伪装NVIDIA GPU，不加入现有CUDA调度槽位。纯CPU节点可运行Agent并上报主机信息，不因缺少nvidia-smi而失败。
本轮兼容旧GPU槽位模型；完整NPU纳管需要继续实现芯片/逻辑设备映射、DCMI或经机型验证的采样、CANN运行时和设备隔离、Worker执行与SSH纳管流程。GPU共享能力不能直接推定为NPU共享能力。实际NPU采样验证不等于NPU任务执行验收。
参考：https://github.com/Ascend/agent-skills/blob/master/skills/npu-smi/references/device-queries.md 和 https://www.hiascend.com/doc_center/source/en/mindx-dl/300/dluserguide/clusterscheduling/dlug_guide_03_000105.html 。
