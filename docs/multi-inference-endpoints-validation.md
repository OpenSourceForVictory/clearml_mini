# 多地址推理分配验收

2026-11-02，变更 `multi-inference-endpoints`。

## 自动化验证

- 后端全量首轮：175 passed，22 skipped（独立 PostgreSQL/挂载专项需单独配置）。修复旧 CSV 无 config 的兼容问题后通过。
- 新增地址专项：12 passed，覆盖列表校验、Cosmos 显式共享、跨用户互斥、忙碌首地址绕过、共享均衡、无 slot 不占地址、claim 重放、清理前保留占用、重试历史以及五种撤销路径。
- 独立 PostgreSQL 既有集成回归：17 passed，6 skipped（使用其他专用数据库/挂载环境的专项）；新增并发与迁移分别单独执行。
- `test_endpoint_race.py`：6 个并发调度入口仅产生两个不同独占地址；双 claim 输入一致；claim/cancel、cleanup/schedule/retry、pause/claim 竞争通过。清理前抢先重试可以返回 409，确认清理后重试成功，符合既有协议。
- `test_endpoint_migration.py`：旧单地址 RUNNING/SUCCEEDED 回填、PENDING 留空、字段成对约束、Alembic check、多地址未排空时拒绝降级。
- 浏览器首轮 31 passed/2 failed；发现列表标签歧义及比较字符串多余引号，修复后相关 11 项复测通过。覆盖真实单回合试跑、三任务正式执行、无资源排队、草稿、迟到响应、幂等提交和多地址模式传递。

## 真实 Docker 协议验收

隔离 PostgreSQL `mini-eval-endpoints-pg`、API 18090，两个测试推理服务 19091/19092，两个 Docker 客户端 slot。仅使用 SDK demo，不代表 RoboTwin/Cosmos 模型质量。

初次容器连通性检查失败，服务可连接后重新提交验证；失败执行保留 FAILED，并未计为成功验收。随后双独占三任务集合观察到两个不同服务地址同时执行、第三项 PENDING，最终三项成功。两个 shared 客户端同时访问 19091，另一批 exclusive 任务保持 PENDING，前者完成后后者执行。逐 case 对照服务端请求 parameters 与平台报告，分配地址和实际目的端口一致。

原始本地隔离证据保存在 `.runtime/endpoints-validation/`：`backend.log`、`focused.log`、`pg-regression.log`、`race-complete.log`、`migration-test.log`、`browser-*.log`、`docker-evidence.json`、`requests-19091.jsonl`、`requests-19092.jsonl`。这些文件没有纳入 Git，不含生产凭据；数据库快照仅用于本次隔离验证。

复现单元测试：`.venv/bin/python -m pytest backend/tests/test_endpoints.py -q`。并发测试配置独立已迁移空库 `ENDPOINT_DATABASE_URL`；迁移测试配置独立空库 `ENDPOINT_MIGRATION_URL`。两者不可指向生产。

恢复验收：SIGKILL 隔离 Worker 后重启，执行 ID/Run ID/地址不变，任务完成；失败任务第一次访问 19091，重试时该地址被独占，第二次实际访问 19092。`recovery-evidence.json` 保存两次尝试与服务请求证据。新增地址编辑/删除与预检失效浏览器测试通过，地址相关共 7 项通过。

## 上线与清理

R8000 API/UI 已上线，发布目录 `/home/huawei/clearml_mini/.runtime/endpoints-release-20261102-110612`，旧镜像 `mini-eval-api:before-endpoints-release-20261102-110612`；目录保存 `database-before.dump`（0600）与 `previous-code.tar.gz`。新镜像 `mini-eval-api:endpoints`，数据库版本 `i952d34`，生产 Alembic check 无新增操作。

上线后 local-4090、r8000 两个 Worker 均 online，协议 v6、无隔离、无持有任务；客户端模板与已注册集合和上线前完全一致，旧批次报告可读。未提交生产模型评测，未重启 Worker。回退步骤与多地址排空限制见 [使用文档](multi-inference-endpoints.md)。

最后针对迁移、报告、Cosmos 的 27 项复核通过，TypeScript 检查、Vite 构建、OpenSpec strict 和 git diff --check 通过。隔离 API、Worker、两个推理服务均已停止，测试 PostgreSQL 已备份后删除；18090/19091/19092/55439 均不再监听。
