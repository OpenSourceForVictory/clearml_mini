# 当前版本全量测试（2026-11-23）

产品代码基于 `ef6185a`（Worker 0.21.14，协议 18）。用户在专项回归后授权全量；测试期间只修正前端指南测试定位，没有修改运行中的 Python 业务代码。

## 结果

| 项目 | 结果 |
| --- | --- |
| 统一 Python 全量 | 2369 passed / 126 skipped / 0 failed，1863.14 秒 |
| 覆盖率 | 行 92.02%，分支 83.16%；全部目录及功能组门槛通过 |
| 前端首轮 | 242 passed / 10 skipped / 1 failed |
| 指南专项复测 | 3 passed |
| 前端完整复测 | 243 passed / 10 skipped / 0 failed，3.8 分钟 |
| TypeScript / Vite 构建 | 通过，保留单包大于 500 kB 的提示 |

前端首轮错误：`getByText(/EvaluationClient/)` 同时匹配指南说明与代码示例，Playwright strict mode 拒绝歧义选择。修正为分别断言说明列表和示例代码；直接协议同步检查事件及 final 示例字段。不是删除断言或隐藏重复文案。已同步本记录及覆盖率指南；产品指南和 Skill 无运行行为变化，无需修改。

Python 共 11 条警告，来自 Starlette/AnyIO 弃用、Pydantic 字段属性提示及多线程进程 fork 提示；未作为失败隐藏。详细门槛见 [覆盖率汇总](coverage-summary.md)，未执行条件见 [跳过明细](skips.md)。

## 环境与执行方式

Python 使用仓库 `.venv`，与 `docs/code-coverage.md` 同一入口：

```bash
PYTEST_ADDOPTS=--import-mode=importlib \
TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55473/regression \
LOG_TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55473/postgres \
MIGRATION_DRIVER_TEST_URL=postgresql+psycopg://postgres@127.0.0.1:55473/postgres \
./.venv/bin/python -m scripts.test_coverage --output .runtime/full-regression-20261123/python
```

仅为本轮创建临时 PostgreSQL 16.8 容器，端口仅绑定回环地址；预先将 regression 测试库迁移到 head，迁移驱动及日志测试在该临时服务器创建各自数据库。测试结束容器已移除。没有连接业务数据库，不要将上述测试变量指向生产。

前端使用 Node 22 和独立 Vite 18083 端口，运行全部 Playwright 测试。测试服务已停止。10 项条件跳过要求真实部署、凭据、推理服务或设备环境，未在此次浏览器测试中启用。

## 证据与边界

原始证据保留在 Git 忽略目录 `.runtime/full-regression-20261123/`：

- `python.log`、`python/tests.xml`、`python/test-exit.json`、标准 coverage JSON/XML/HTML 和 summary。
- `frontend.log` / `frontend.xml` 首次失败，`guide-recheck.log` 指南复测，`frontend-final.log` / `frontend-final.xml` 完整复测。
- `build.log` 构建结果。

没有排除业务代码、降低门槛或合并历史覆盖率。前端与未插桩独立进程不计入 Python 覆盖率；本轮没有新增 GPU/NPU 模型、真实 systemd/Docker 矩阵或现场部署验收。跳过数不是通过数，代码覆盖率不是现场根因证明。

测试过程中用户明确了新时间展示需求：非逐回合启动统计整次任务，逐回合启动统计整次任务及每回合。现有界面只有部分业务 runtime 展示，该需求尚未实施，也不属于本次测试通过的能力。后续应区分排队时间、平台执行时间及业务上报耗时，对重试分别统计，对历史缺失值显示未记录。
