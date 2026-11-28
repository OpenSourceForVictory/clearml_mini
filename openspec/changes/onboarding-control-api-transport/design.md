# 设计

`Control` 只把 `api`、`api-gateway`、localhost 与回环 IP 识别为内部地址，为这些请求构造空 `ProxyHandler`。其他主机仍用 `urllib` 默认代理处理器；自定义内部域名由部署方设置 `NO_PROXY`。

HTTP 503/504 保留 `control_api_rejected_<status>`，执行器日志增加内部路由路径、状态及固定白名单中的 503 原因。未知正文或 504 HTML 一律记为 `unavailable`，不写响应正文、授权头、令牌、SSH 数据。路由日志用于和网关访问日志关联，不将其当成根因判定。
