# Tasks

## P0 — 基础可运行
- [x] 建立 Git 项目及 OpenSpec 变更目录，并通过 OpenSpec 格式校验。
- [x] 建立确定性 mock Runner；完成无 GPU 的重复性测试和容器运行验证。
- [x] 固定 ClearML SDK 版本并验证 Python 3.11 后端镜像构建。
- [x] 补齐 PostgreSQL 的 Alembic 初始迁移、索引、UTC 时间戳及数据库集成测试。
- [ ] 完成管理员引导、用户鉴权、任务提交与所有 API 合同测试。
- [ ] 完成真实 ClearMLAdapter、远程 Runner repo/commit 配置及 Agent 可执行演示。
- [ ] 完成 Compose 控制节点启动、健康检查和真实服务 smoke test。

## P1 — 调度与故障恢复
- [ ] 实现单领导者调度、跨用户轮转、FIFO、全局/用户限额与资源池匹配。
- [ ] 实现逐卡槽位注册、物理 UUID 去重、Worker 在线 TTL 和节点资源限额。
- [ ] 实现持久化投递 intent、稳定 attempt 标记、崩溃恢复与 ClearML 对账。
- [ ] 实现任务状态同步、超时、Worker 断联宽限期和故障注入测试。
- [ ] 通过多用户、多机型、多卡与单卡并发验收。

## P2 — 产品与运维
- [ ] 完成取消、重试、日志、指标 API 及权限测试。
- [ ] 完成 React 登录、任务、详情、Worker/Pool 和管理员界面。
- [ ] 补齐部署脚本、中文运维文档、OpenAPI/curl 示例及全部 CI 检查。

## 真实评测验收
- [ ] 固定 RoboTwin 上游版本、Runner 镜像、资产与 checkpoint 契约。
- [ ] 在远程 GPU Agent 上完成 RoboTwin 评测、结果展示、取消和重试验收。
