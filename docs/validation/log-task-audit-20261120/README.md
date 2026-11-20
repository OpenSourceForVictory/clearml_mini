# 独立日志历史任务复核

按 OpenSpec 原条款检查实现和可观察断言，避免把已有基础误列成完全未实现，也避免把复合待办直接全部勾选。冻结版本全量执行已结束；下表关联测试均通过且未跳过，已据此完成五项对应条款。整套另外6项适配器上下文测试失败，已修正并正在重跑全量，不将此审核写成全量通过。

| 条目 | 实现及断言证据 | 验证边界 |
| --- | --- | --- |
| 1.1 身份契约 | `test_execution_locations.test_log_identity_and_cursor` 区分执行、回合、代次；`test_attempt_location_no_cleanup_filter_and_no_current_path_guess` 明确 run_id != execution_id；`test_new_log_protocol_negotiates_only_complete_capability` 验证能力协商；真实 NPU 同名双实例证据见 npu-process-logs-platform-20261119 | 两实例的隔离取自执行 ID，不以服务名称命名；没有宣称所有任意业务程序都能被自动拆分 |
| 2.1 文件与恢复 | `test_store_rotation_execution_budget_and_uncommitted_tail` 检查实际文件总字节与未提交尾部；`test_actual_writer_crash_recovers_pending_rotation` 覆盖真实进程中断；primitives 的编码、分页、篡改、索引/范围用例 | 实际进程中止恢复，不是断电持久性证明 |
| 2.2 有界采集 | `test_sink_failure_keeps_draining_real_child_and_exit`、`test_capture_disk_write_failure_keeps_exit_and_marks_gap`、`test_slow_sink_queue_overflow_and_bounded_drain`、`test_sustained_output_slow_consumer_stays_memory_bounded` 与 stderr tail 断言 | 错误注入与受控慢 sink；不承诺解除内核不可中断 I/O |
| 3.4 历史读取 | failures 中 restart/unsent catalog/API outage/deleted execution/independent background thread 用例，pipeline 的 terminal scope discovery 与 slot routing；已有真机 Worker 重启历史日志读取 | 只恢复索引/读取，不重执行业务；旧根不能从新配置反推 |
| 4.1 有界邮箱 | `test_log_read_terminal_auth_and_idempotent_response`、`test_read_admission_bounds_cross_node_and_cleaned_tombstone`、`test_postgres_small_pool_offline_node_burst`；实时尾部补测保留旧订阅、在途合并及固定下载缓存 | 每用户/节点/全局上限保持；负载记录不作为生产 SLA |

其他复合条款仍有待补证据，尤其 10000 回合是构造历史 scope 索引的边界测试，并非真实执行 10000 个业务回合；完整取消矩阵、所有旧镜像/权限组合、读取中的竞态与全部 UI 状态不能仅凭现有通过数推断完成。
