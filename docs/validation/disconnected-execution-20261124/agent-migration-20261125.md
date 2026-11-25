# Agent 观察分表迁移兼容，2026-11-25

`ad074e5` 及 `agent_state.py` 的实现早于本次核对。观察记录与期望配置分开保存；升级从旧 Worker JSON 回填，降级只合并与当前会话相符的观察值，旧会话及旧序号不能覆盖新状态。操作时必须暂停旧 API 写入、备份、迁移后再启动新 API，不能同时由新旧 API 写同一数据库。

隔离 PostgreSQL 运行 `backend/tests/test_agent_observation_isolation.py`：9 passed、0 skipped，覆盖空库升降级再升级、历史配置和最新观察合并、旧会话隔离、缺失记录回退、失败回填的事务回滚与修复后重试。`test_resource_reporting.py::test_agent_source_identity_and_legacy_rollback` 与 `::test_one_agent_updates_multiple_gpu_slots_by_uuid`：2 passed、0 skipped，覆盖旧 `resource_protocol=0` 继续由 Worker 上报、协议 1 由 Agent 上报及多卡身份匹配。没有改动迁移脚本或数据库版本；这项验收不代表恢复执行的新迁移已在生产启用，也不替代全量覆盖率。
