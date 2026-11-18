## Context
通用请求函数在判断状态前调用 res.json。500 的 Internal Server Error、网关 HTML 和空响应会产生 SyntaxError。
## Decisions
失败响应 JSON 解析失败时回退到包含 HTTP 状态码的通用提示，不展示代理 HTML 或堆栈；401 在解析前处理会话过期。成功 JSON 保持返回语义；异常成功响应给出明确格式错误。204 支持空响应。删除确认明确节点工作目录不释放磁盘空间。
## Validation
浏览器模拟 500 文本、502 HTML、空响应、JSON 409、重试成功；API 完整关联记录在 SQLite 外键开启下删除。保留失败证据；不声称已修复现场服务端 500，不运行全量或真实 PostgreSQL。
