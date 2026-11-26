# Design

锁分类：全局调度advisory锁保护跨执行分配/释放与就绪失效；Run锁保护执行身份、状态和租约；Slot锁保护设备最新观测；grant锁保护恢复序列、身份和控制快照；日志游标锁保护日志追加。不以API进程隔离替代数据库互斥。

心跳已有Run NO KEY UPDATE和Slot行锁，移除STARTING前置fast_lock。取消共用入口按最新attempt锁Run，刷新Job再决定状态，与启动串行，防止迟到启动覆盖取消。调度超时路径已SKIP LOCKED并复查Run，测试保持此契约。

恢复prepare只更新原执行许可，按Run→grant持锁，移除全局锁。reconcile单执行STARTING/RUNNING/终态观测不涉及资源释放，移除无条件/非RUNNING全局锁；过期的服务恢复会清除health_ready及探测epoch，保留scheduler→Run→grant顺序，预读后服务租约跨过期边界则回滚503重试，不能拿Run后再拿全局锁。Run改为NO KEY UPDATE，允许引用该Run的日志外键检查。取消/终态优先和恢复幂等不变。

本轮保留：实际claim/bind、旧complete（同时cleanup）、recovery完成/cleanup、未启动释放、服务probe-session、池成员修改、批次删除与调度tick。前者涉及资源/跨行状态，后者事务范围广；不在没有证明状态快照与释放安全的情况下机械删锁。报告如实区分可后续缩小点，不宣称已消除所有竞争。

验证先真实PG锁持有注入：不相关全局锁下启动/prepare/reconcile200；同Run状态竞争互斥；取消与启动双顺序、超时与启动双顺序、迟到旧执行和重评测、服务过期仍需全局锁、非过期服务无阻塞、日志外键不被控制更新阻塞。再跑原100用户/100Worker/100任务混合对照，不改变通过标准。无全量或小时长测。
