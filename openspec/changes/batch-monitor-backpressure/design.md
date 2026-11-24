## Context
当前 usePoll 在请求结束后固定2秒再请求，不重叠，但无失败退避和文档可见性控制。api 丢弃 HTTP reason/Retry-After。DiagnosticContext 为每个任务重复生成相同 managed 候选。
## Goals / Non-Goals
降低无意义读取、保留诊断正确性；不修改调度锁、请求预算，不用扩容掩盖慢查询，不引入跨请求缓存。
## Decisions
- ApiError 保留 status、reason、Retry-After 毫秒；支持秒数和 HTTP 日期，并允许可选 AbortSignal，原调用参数兼容。错误文本保留原 detail。
- 通用 usePoll 支持可选 intervalMs，默认仍2秒；批次 monitor 显式5秒。失败指数退避并加有限抖动，自动延迟上限30秒；服务端更长 Retry-After 优先。成功复位计数，保留旧数据，显示明确旧数据提示。401停止自动轮询，重新登录由界面生命周期恢复。
- document hidden、卸载、路由或 enabled/context visibility 变化取消在途读取并清理定时器；不把取消显示为请求失败，恢复可见立即读取，避免旧响应覆盖新页。
- DiagnosticContext 缓存 managed endpoint 状态，key=批次ID+规范排序后的服务key集合（缺省与空集合区分），只在当前上下文有效。普通endpoint不缓存。上下文的资源占用固定，holdings计算一次；scheduler继续使用原实时选择函数。
## Risks / Trade-offs
五秒正常刷新牺牲少量展示时效换取降载。中止 fetch 不承诺取消已进入后端的SQL。请求内缓存不用于实际分配；下一请求重新核对健康与租约。后台调度持锁、数据库容量仍需独立定位。
## Migration Plan
更新 API 与前端，无迁移/Worker更新。回滚对应代码即可；API错误类型对现有 String(error)兼容。
