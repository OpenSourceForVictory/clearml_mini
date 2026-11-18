## Why
删除批次返回 HTTP 500 文本时，前端无条件解析 JSON，掩盖 HTTP 错误。现场后台异常尚未复现，需要明确验证边界。
## What Changes
- 对非 JSON 失败响应显示 HTTP 状态码，不显示解析器异常；保持 JSON 业务错误翻译。
- 验证删除失败保留批次及选择、允许重试；补充完整关联数据删除测试。
## Capabilities
### New Capabilities
- `api-error-feedback`: API 错误响应的可靠展示。
### Modified Capabilities
无。
## Impact
frontend/src/api.ts、批次测试、操作指南及 Skill。无需数据库迁移，不改变删除准入规则。
