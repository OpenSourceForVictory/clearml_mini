# Design

## Context
Worker 在 create 前写 created=true，防止不确定执行被重复创建。首次失败上报遇到临时 API 故障时 journal 保留，下次恢复只能看到 created=true + unit 不存在，导致原始错误丢失。

## Goals / Non-Goals
保留首次终态失败请求、停止/清理原执行、增加可核对身份。未知 unit 消失不能自动重新启动；此修正不宣称解释所有双实例现场故障。

## Decisions
- 使用现有独立执行 journal 保存 terminal_failure 原请求，先持久化再调用 complete；恢复获取租约和取消状态后重报原请求，不进入创建流程。首次错误与退出码保持不变。
- 服务任务携带可选 service_instance_id；宿主机 missing unit 信息包含 execution_id、service_instance_id、unit 和 journal-created/control-plane-running 来源，不包含环境变量或凭据。
- 保留原幂等完成及 404/409 清理语义，不删除 created 标记或以同一 execution_id 重建进程。

## Risks / Trade-offs
- 已丢失的旧错误无法恢复 → 旧 journal 仍安全拒绝重复执行并给出身份诊断。
- 首次启动原因可能在系统、权限、端口、资源限制 → 保留真实原始错误，不将双实例配置本身判定为非法。

## Migration Plan
无数据库迁移。更新 Worker 生效；更新 API 后新 claim 增加可选服务实例 ID。旧 API 不提供此字段时仅 execution ID 仍可诊断；旧 Worker 忽略附加字段。未发布前不声称现场恢复。

## 启动标记语义核对
created/launch.started 是外部副作用前的尝试标记，不能移到成功之后，否则崩溃窗口允许重复执行。运行与就绪来自独立观察。保留标记兼容旧 journal，首次失败必须在停止操作之前持久化，防止清理异常覆盖；失败退出同样保存退出码。不同卡 Worker 测试核对独立 journal、unit 与单实例清理，真实 systemd 模拟服务不代表真实加速卡业务。

## 2026-11-20 探测会话临时故障
真机复现：service-probe-session 返回 503 后停止已启动进程，恢复报 unit missing。将会话申请纳入启动租约续期保护；503/网络超时在原启动时限内重试，取消、租约失效和非临时错误仍终止。保持执行身份，不重新创建进程。适用于 host 与 Docker。
