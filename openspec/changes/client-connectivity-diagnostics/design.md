## Decisions
check 保留异步接口与异常语义，内部探测返回方式/状态供日志使用。成功 INFO、失败 WARNING，记录毫秒耗时、超时上限、异常类型和分类。verify 上报包含相同分类的 RuntimeError，保持 client_service_unreachable 前缀。
仅记录 URL origin，不记录用户信息、路径、查询、片段、响应正文或原始异常文本。分类包括 timeout、dns、connection_refused、tls、websocket_handshake、invalid_endpoint、dependency、connection_error 与 unexpected_error。不将 405 当业务就绪成功；它只表示 HEAD 可达。
## Validation
本地 HTTP 服务返回 405 并设置不可达代理，验证 check 通过；失败分类、日志脱敏、默认超时、取消保持原义；现有服务执行测试确保失败禁止启动。无现场节点发布或真实模型验收，不跑全量覆盖率。
