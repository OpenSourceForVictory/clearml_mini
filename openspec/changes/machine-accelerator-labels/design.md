## Context
机器管理使用 gpu_count 统计全部已登记槽位，NPU 也在其中。
## Goals / Non-Goals
纠正客户端和服务端的设备数量标签。不增加后端字段或推断设备类型。
## Decisions
概览改为“加速设备总数”，表头改为“加速设备数量”。指南标明 GPU/NPU 合计不等同回连验收；具体类型使用现有设备信息。
## Risks / Trade-offs
不提供按设备类型分组的额外统计。
