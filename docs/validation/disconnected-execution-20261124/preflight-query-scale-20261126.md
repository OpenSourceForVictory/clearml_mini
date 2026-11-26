# 多任务、多节点预检查询专项

触发条件：一次向导预检会把所选集合的每项任务与候选设备在 API 内检查，不逐节点向 Worker 发 HTTP 请求。原实现对每项任务重新查询同一客户端模板；托管服务的只读诊断又逐卡和逐项重复查同一节点端口。节点、任务和同时预检的用户增加时，这些查询会延长数据库连接占用，并可能遇到入口 `request_concurrency`、连接池等待或请求预算 503。

修正：`resolve_batch` 在单次请求内持有客户端模板对象；托管服务预检的 `Planner` 仅在只读实例内缓存 `(worker, avoid ports)` 的端口诊断。正式调度 Planner 不启用端口缓存，仍在预约时查询最新占用；提交也重新校验，不使用预检结果预约资源。

隔离 SQLite API 量测：25 个模拟四卡节点加基线节点，共 101/103 张候选卡；分别提交 1、10、50、100 项的普通/托管服务预检。普通 100 项由 107 条 SQL 降至 8 条，托管服务 100 项由 323 条降至 48 条；同一次量测中 100 项响应分别约 3.56 MB、1.55 MB。四种规模均返回 200，普通/托管服务 100 项耗时约 0.21/0.23 秒。量测节点是测试数据，业务未启动，使用 SQLite、单用户顺序请求，不能据此推断 PostgreSQL 生产并发容量或保证不出现 503。响应仍逐项包含候选卡诊断，大小随任务和卡数增长。

回归测试 `backend/tests/test_preflight.py::test_many_cases_reuse_selected_template_query` 和 `backend/tests/test_orchestrations.py::test_many_managed_cases_reuse_read_only_port_diagnosis` 比较 1 与 20 项，验证 SQL 数量不随任务数增长，并核对返回项数；预检/编排全组 47 项通过。服务实例补足、实例数量及就绪检查相关 27 项也通过，覆盖正式调度仍实时查询端口的路径。另用一次性 PostgreSQL 16.8 容器运行 `backend/tests/integration/test_orchestration_race.py`，调度与提交并发竞争 1 项通过；这并非并发预检容量测试。进一步的 PostgreSQL 并发预检与页面大响应验收仍待做，不能把这项局部查询优化计为 OpenSpec 5.2 完成。
