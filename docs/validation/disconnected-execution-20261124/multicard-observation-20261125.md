# 多卡观察与配置竞争，2026-11-25

触发条件：同一节点多个卡位同时发送空闲心跳，Agent 同时上报采样，管理员又更新期望配置。旧空闲心跳无论是否携带配置回执都会锁整机 Worker 行；Agent 的 `busy` 响应还会按卡逐一查询占用，随卡数放大。

修正：仅携带存储、容器运行环境或宿主机环境回执的空闲心跳锁 Worker 行，保证回执与配置修订串行。普通心跳只锁对应 Slot；执行心跳仍先锁 Run 再锁 Slot。Agent 忙闲状态改成整机一次有界存在性查询。Agent 观察依旧锁独立的 `node_agent_observations` 行；有效采样写对应 Slot，重复相同采样不会再发 Slot 更新。序号/会话、样本原始时间和配置修订语义不变。

验证：`test_node_agents.py`、`test_heartbeat_contention.py`、`test_storage.py`、`test_image_runtime.py`、`test_host_environments.py`、`test_execution.py` 合计 46 passed、0 skipped；隔离 PostgreSQL `test_node_agent_race.py` 追加三卡场景后整组 7 passed、0 skipped。新测试在配置 Worker 行被持有时要求三个普通心跳仍可完成，检查配置/采样值及时间不丢失，并用 SQL 计数断言四卡忙闲判断只有一次查询、重复样本不更新 Slot。同卡上的 Agent 新采样与该卡 Worker 心跳仍由 Slot 行串行，这是对应卡位状态原子更新所需；不同卡和整机配置不再无条件共享此写锁。此为隔离数据库并发验收，不代表生产 100/200 Worker 容量或全项目覆盖率；后者仍归 5.2/5.4。
