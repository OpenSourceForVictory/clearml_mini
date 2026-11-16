# Proposal

## Why

现有服务健康检查仅支持 HTTP 状态码和 TCP 连通性，无法确认纯 WebSocket 服务的业务就绪状态；同步探测与固定报告有效期也限制了扩展。需要通用、非阻塞且可诊断的就绪契约，让调度器正确等待服务，同时保持已有模板兼容。

## What Changes

- 统一 TCP、HTTP、WebSocket 握手及消息检查配置，支持独立健康端口、受限响应匹配与认证引用。
- 独立有界探测执行器、成功失败阈值、报告有效期、执行代际和序号校验。
- 区分启动中、就绪、未就绪、未知、停止与启动失败；客户端调度复用统一就绪判断。
- 客户端启动前验证已分配地址的连通性，网络故障按客户端节点诊断，不污染全局服务健康。
- 模板编辑/查看/复制、实例状态与错误修复入口同步完善；增加协议、恢复、调度、清理和 UI 测试。
- 保留旧 HTTP/TCP 模板和执行快照语义；新能力通过 Worker 能力准入，禁止降级探测。

## Capabilities

### New Capabilities
- `service-readiness`: 通用服务探测、就绪生命周期、调度门控及可操作诊断。

### Modified Capabilities
无。现有 platform 主规范未定义服务健康检查；本变更补充专门能力，不修改其历史评测准入要求。

## Impact

涉及 backend/app/templates.py、services.py、service_dependencies.py、预检和服务编排；worker/services.py、client.py、Worker 发布/纳管依赖；运行模板与服务详情 UI。需要健康状态持久化字段迁移和探测协议能力协商。业务 SDK、GPU 分配与 Agent 节点监控职责不变。新增 WebSocket 库须锁定兼容版本并纳入安装与升级包。
