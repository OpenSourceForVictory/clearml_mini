# 控制面通道与事务核对（2026-11-23，开发中）

对应 OpenSpec worker-control-plane-backpressure。只说明当前工作树，不表示现场已发布。断联继续执行、终态补传仍在待办，当前 Worker 租约到期仍停止。

## 请求与后台分类

| 请求/任务 | 预算类别 | 锁及交付边界 |
| --- | --- | --- |
| Worker heartbeat | control | 运行续租 Run→Slot；STARTING 转换先 fast_lock；空闲心跳仍 Worker→Slot 保护配置合并 |
| service-health | control | 服务状态及探测序号的局部锁，旧报告不能恢复新状态 |
| service-probe-session | control | 建立探测身份仍参与调度互斥 |
| complete / cleanup | control | 全局资源转换锁→Run及相关资源；确认停止才可复用；当前 complete 仍要求结果齐全 |
| jobs/*/cancel、batches/*/control、admin/services/*/control | control | 鉴权与操作同类会话；保留原资源事务与权限检查 |
| agent/stop-permit | control | 全局锁→Worker，核对期望版本、无在途任务后才允许停止 |
| agent/heartbeat | observation | 观察行序号/会话锁；刷新读取 Worker 配置不锁配置行；资源协议启用时仍写 Slot |
| executions/*/episodes、task-result | result | Run 局部锁保护幂等；无全局调度锁；不与大日志共享池 |
| 日志、产物、目录、历史读取、Web、领取、注册及其他操作 | ordinary | v2 日志用日志状态行锁及执行专属终态事务锁，旧 `/logs` 仍与心跳争 Run；产物仍锁 Run；领取 fast_lock；所有 HTTP 含静态资源在同步派发前限并发 |
| 调度 loop | scheduler | 独立连接池，不经过 HTTP 门；仍保留调度预约全局原子性 |
| 迁移/备份 | 外部独立预算 | 不继承在线 SQL 短超时；须在总数据库容量中另留余量 |

路由分类仅按服务端准确路径与 POST 方法，额外路径段及其他方法不能伪装成控制通道。入口拒绝在鉴权/借连接前发生，不能用客户端 Header 自行提升优先级。鉴权使用分类后的同一依赖 Session。

所有在线类别有独立准入上限；只有 control 允许短时 FIFO 等待，等待名额最多控制池大小四倍且不占数据库连接，超时/取消移除。普通、观察及结果饱和不借控制预算。同步线程默认额度不足时提高到在线准入总量加四个余量；该机制基于当前每请求同步工作顺序执行的调用链，不提供 CPU、数据库磁盘或未知扩展后台任务的硬隔离，后续新增并行同步工作必须复核。

## 已确认的剩余风险

- Agent 分表解除普通配置写与监控共写 Worker JSON，但 resource_protocol=1 仍写 Slot；同一执行大量日志也可能竞争 Run。不得宣称全部行锁已消除。
- 调度有独立连接，但调度事务仍可能长时间持全局锁；完成/清理有在线有限等待，不能据此宣称后台调度永远及时。
- 当前 Worker 主循环仍存在先交付结果/产物再 complete 的路径，持续批量故障可能拖住收尾；完整解耦需要后续恢复/补传协议。
- 每条业务 SQL 的两个设置往返合并为一次 set_config 查询，累计截止和事务本地作用域仍保留；不能把减少一个往返换算为固定吞吐收益。
- Retry-After 后增加随机分量，保留服务端最早重试时间与当前租约截止；旧 API 常态兼容不等于旧 Worker 过载安全。

## 证据索引

- 原型首次失败、100/200 档短时负载、被中断的全量记录：同目录 README.md 和 load-*；均不覆盖完整混合负载。
- 本轮 priority-focused.log：114 项通过，覆盖五类预算、完成清理隔离、实际 PG 超时/死锁/HTTP 提前断开、重试及 Agent 状态。
- 本轮 isolation-focused.log：47 项通过，包含真实 PG 配置锁独立上报、迁移升降级、会话序号保护、执行局部锁和 Worker 过载故障注入。
- 随后的 final-focused-first.log 继续覆盖真实同步处理饱和、Agent 配置/上报竞争及资源上报；首次152通过/1失败（旧JSON断言），修正后相关复测58通过；固定最终应用代码 scope-final.log 109通过。不能将各组重叠测试直接相加。

用户要求先完成专项，不运行全量。专项覆盖率仍以项目配置采集，但不足以声明全目录/关键功能门槛达标；独立进程没有合并插桩时不计入。

`GET /healthz` 使用无数据库、无同步线程的固定异步存活响应，不受普通入口饱和影响，避免正常背压导致存活检查误失败；`/readyz` 仍检查数据库并受普通预算限制。
