## ADDED Requirements
### Requirement: HTTP 错误可见
前端 SHALL 在失败响应不是 JSON 时显示 HTTP 状态码，不能用 JSON 解析异常替代错误；删除失败保留批次及比较选择。
#### Scenario: 删除返回文本 500
- **WHEN** 删除批次返回 500 文本 Internal Server Error
- **THEN** 界面显示请求失败和 500，保留批次并可重试
#### Scenario: JSON 业务错误
- **WHEN** 删除返回 JSON 409 服务未清理错误
- **THEN** 界面继续显示对应中文业务提示
#### Scenario: 未认证文本响应
- **WHEN** 非登录接口返回文本 401
- **THEN** 前端仍执行会话过期处理
