# Design
优先压缩临界区工作量，而非拆开资源检查与预约事务。reconcile使用select(Run,Job,recovery).join(Job)，本轮持有Job引用避免弱identity map逐次丢弃和重复读取；后续Run SKIP LOCKED+populate_existing复查不变。tick统计active也一次join Job，保持cleanup_confirmed=false终态占用计数，删除未使用Pool查询。pending仍按原过滤/顺序读取；batch查询限定pending中的非空batch_id，空队列不读取历史批次。所有数据库读取仍在取得全局锁之后，同一Session、同一事务，资源写入前不会引入新的锁外过期窗口。

测试覆盖历史批次数增长不增加加载对象、活动执行增长不产生逐Job/Pool查询、终态未清理占用、暂停恢复/优先级/公平性、重评测、预约期限、取消/超时竞态与事务回滚。真实PG前后同输入重复测量；脚本回滚样本不含提交耐久成本，不宣称生产SLA或所有编排路径已优化。上线仅API，不升级节点。
