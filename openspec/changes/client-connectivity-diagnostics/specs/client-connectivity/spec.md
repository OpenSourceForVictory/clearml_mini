## ADDED Requirements
### Requirement: 连通性诊断
Worker SHALL 记录启动前连通性检查的安全目标、方式、耗时及成功响应状态或失败类型，失败任务保留原因分类；不得记录 URL 凭据、路径、查询或原始异常中的敏感信息。
#### Scenario: HEAD 405
- **WHEN** Worker 收到 HEAD 请求的 HTTP 405 响应
- **THEN** 检查通过且日志包含 405
#### Scenario: 连接超时
- **WHEN** 检查超时
- **THEN** 日志和任务错误包含 timeout 及超时上限，禁止启动业务
#### Scenario: 失败脱敏
- **WHEN** 异常携带敏感地址信息
- **THEN** 日志及任务错误不输出原始异常文本或敏感 URL 部分
