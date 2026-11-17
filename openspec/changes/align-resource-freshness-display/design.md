## Context
调度 `telemetry_fresh` 要求样本在过去 15 秒内且空闲显存、利用率非空；`main.workers` 旧判断只要求时间差小于 15 秒。
## Decision
管理员列表直接调用调度函数，保持已有字段与 TTL。主机监测独立，不参与 GPU 新鲜度。
## Verification boundary
隔离 API 测试覆盖有效、过期、未来和字段缺失；不代表现场时钟或 GPU 状态已修复。
