## ADDED Requirements
### Requirement: WS 传输可达
Worker SHALL 对所有路径的 WS 端点使用 TCP 检查、WSS 使用验证证书的 TLS 检查，不发送 HEAD 或业务 WebSocket 握手；保持总超时和地址安全校验。
#### Scenario: 服务拒绝 HEAD
- **WHEN** WS 服务可连接但拒绝 HEAD
- **THEN** 连通性检查通过且没有发送 HEAD
#### Scenario: TLS 不可信
- **WHEN** WSS 证书不受信任
- **THEN** 检查失败并保留 TLS 分类
#### Scenario: DNS 解析到禁止地址
- **WHEN** 解析结果包含禁止访问的地址
- **THEN** 在建立连接前拒绝该端点
