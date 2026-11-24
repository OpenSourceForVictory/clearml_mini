# 服务实例补齐公平性验证

旧实现失败复现：5 failed、6 passed，包含低优先级先补齐、创建时间失效、未保存游标及单用户占满剩余位置。原始 before.log 保留，后续去掉一个与 inactive 重复的初稿 budget 用例，预算继续由既有 test_desired_service_instances 覆盖，未以重复用例增加通过数。

新实现按独立用户轮转、用户内优先级/创建时间/ID 排序，每用户每轮最多一个；每次依旧重新构建 Planner 检查容量，沿用全局锁及清理账本。增加 10 项真实 ORM/预约测试，覆盖跨 tick、优先级、同级时间、不可满足队首、暂停/删除/禁用和事务回滚。

第一组关联专项 222 passed、15 skipped、0 failed，450.63 秒；跳过为未指定一次性 PostgreSQL 的用例。随后在既有独立测试服务器上逐例创建随机库，运行 db_budget 和 execution_lock_scope：83 passed、0 skipped、0 failed，66.24 秒，覆盖前述 15 项；两组有重复测试，不将 305 次通过声称为独立用例数。

同一生产源码使用 project.coveragerc 插桩，合并上述两组后以 scripts.coverage_report 的统一口径统计：调度与资源完整功能组行 94.37%、分支 83.50%，达到关键门槛。全项目门禁仍失败（24 条），本次仅专项，不合并历史全量数据，也不把前端和独立容器计入覆盖率。原始覆盖数据在 .runtime/service-refill-fairness/。

无需数据库迁移或 Worker 更新。当前代码验证不等于已发布；没有新增真实模型或长期压力验收。用户指南和 docs/batch-controls.md 已说明补齐策略，项目 Skill 已更新实现索引。
