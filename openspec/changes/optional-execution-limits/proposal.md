## Why
用户需要长时间评测可取消总执行时限，并显式选择不限制 CPU/内存。需要把调度最低预约量与运行硬上限明确区分，避免用资源0跳过容量判断。
## What Changes
客户端 timeout_seconds=0 表示不限；CPU/内存按正数最低要求预约，新增可选标志取消运行硬上限。前端增加明确选项，保留模板最低与锁定约束。旧正数及遗漏字段不变。旧 Worker 拦截新不限配置。
## Capabilities
### New Capabilities
- `optional-execution-limits`: 可选执行与资源限制。
### Modified Capabilities
无。
## Impact
提交校验、预检准入、资源预算、宿主机 systemd、Docker 启动、前端和测试文档。无数据库迁移，Worker 协议16（0.19.0）。
