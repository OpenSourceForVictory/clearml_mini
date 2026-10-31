# 执行数据保留验证记录（2026-10-31）

变更：`worker-disk-retention`。功能及回退方法见 [操作文档](worker-retention.md)。

## 自动化测试

- 后端回归：166 项通过；其后补充和修改的保留接口测试 5 项、Worker 文件系统测试 14 项分别通过。补充结果不与全量结果简单累加。
- PostgreSQL 全组：20 项通过；迁移增加进行中操作降级保护后单独通过，增加重试与确认竞争后并发测试再次通过。
- 浏览器全组：31 项通过；保留策略/盘点的 3 项重新验证通过，另补迟到预览响应隔离测试 1 项通过。
- TypeScript、Vite build、Alembic check、OpenSpec strict 与 Git whitespace 检查通过。
- Linux 挂载测试在隔离的 privileged Docker mount namespace 内执行：目录与文件的同文件系统 bind mount 均被识别，扫描和删除不跨挂载，外部资产内容保持不变。

可重复运行的测试：

```bash
.venv/bin/python -m pytest backend/tests/test_retention.py backend/tests/test_worker_retention.py -q
# 只使用已迁移的独立临时 PostgreSQL 数据库，不能指向生产。
RETENTION_DATABASE_URL=postgresql+psycopg://... .venv/bin/python -m pytest backend/tests/integration/test_retention_race.py -q
MIGRATION_DATABASE_URL=postgresql+psycopg://... .venv/bin/python -m pytest backend/tests/integration/test_retention_migration.py -q
RUN_RETENTION_MOUNT_TEST=1 STORAGE_PROBE_TEST_IMAGE=mini-eval-client-base:1 .venv/bin/python -m pytest backend/tests/integration/test_retention_mounts.py -q
# 在 frontend 中运行；EVAL_API_URL 指向隔离 UI。
EVAL_API_URL=http://127.0.0.1:18090 npx playwright test retention.spec.ts
```

升级测试从 g730b12 保存旧 Worker，再升级验证默认关闭；启用策略及 running 操作分别阻止 downgrade，终结后可以降级/再升级，Alembic 无模型差异。
并发测试使用真实 PostgreSQL：确认响应重放不重复扩大操作，领取只重投同项，重试创建的新尝试不被旧目录预览纳入；配置切换/开始授权、策略关闭/开始授权串行化，无遗留 idle-in-transaction 会话。

Worker 测试覆盖 intent、rename、接管、部分删除、最后 rmdir 和回执持久化后的崩溃恢复；断线重放回执不删除替代路径。
部分删除的失败回执保留，新扫描/新预览可以重试暂存数据。另验证恢复 journal、停止容器、跨进程 flock、根/源目录替换、权限异常、未知目录、根历史切换、分段扫描、稀疏文件及硬链接。

## 真实 Docker 隔离验收

隔离 API：127.0.0.1:18090；临时 PostgreSQL：127.0.0.1:55439。使用测试推理服务和 `mini-eval-cs-test:1`，属于协议/执行框架验收，不是真实模型准确率评测。

1. CS 客户端完成 3 回合，生成 work/output 和报告；额外 2000 个工作文件用于分段盘点验证。
2. 为已完成目录创建停止的引用容器，盘点受保护；移除测试容器后重新扫描，才可预览。
3. 预览不删除；重复确认同一操作，最终收到 succeeded 回执。另一个执行目录不受影响，assets/cache 哨兵文件未变，任务指标和平台报告仍可下载。
4. 同一节点注册两个独立槽位，批量并发运行两个 SDK 客户端；两个 execution 分属不同 slot。合并预览后，各 Worker 只处理本 slot 的目录，两项均成功。
5. 将隔离 Worker 的最小剩余空间阈值临时设为不可满足值，任务保持排队，未生成自动清理；恢复本机配置后任务成功。测试没有通过填满宿主磁盘制造危险。

原始日志、XML、容量观测和逐项回执位于本机忽略目录 `.runtime/retention-validation/`：`backend.xml`、`postgres.xml`、`docker-acceptance.json`、`multislot-evidence.json`、`pressure-evidence.json`、`production-evidence.json` 等。不把测试凭据或生产配置提交到仓库。

## 生产上线与只读验收

发布目录：`/home/huawei/clearml_mini/.runtime/retention-release-20261031-171746/`。
数据库迁移：`h841c23`；API 镜像：`mini-eval-api:retention`，运行标签为 `mini-eval-api:dev`。
回退镜像：`mini-eval-api:before-retention-release-20261031-171746`。发布目录保存 `database-before.dump`（0600）和 `previous-code.tar.gz`；本机 Worker 旧代码另备份在隔离验证目录。

上线前确认无 RESERVED/STARTING/RUNNING/CANCEL_REQUESTED 或未完成清理的执行。分别暂停节点接单、再次确认空闲，向各自用户的旧 Worker 发送 SIGTERM，由已有 `Restart=always` 服务拉起新进程，再恢复接单。
R8000 与本机 RTX 4090 均报告协议 v6、在线；原服务与密钥配置保持有效。

生产仅请求盘点与各 3 项预览：R8000 首次盘点 15 项，本机 4 项；后续确认两边扫描完成。
两台机器的保留策略都是 revision 0、enabled false、30 天。所有生产清理操作都没有 confirmed_at，提交清理数为 0。
平台地址仍为 `http://192.168.137.223:18000`，入口是“管理设置 → 机器名 执行数据”。

隔离环境已收尾：临时数据库先备份，再停止 API、测试 Worker 和推理服务，删除临时 PostgreSQL 容器及浏览器认证文件。生产 Worker 服务继续在线。既有 RoboTwin 资产后台同步未作为本次测试进程处理。
