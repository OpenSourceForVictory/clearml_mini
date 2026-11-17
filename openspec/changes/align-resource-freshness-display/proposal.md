## Why
计算资源页把未来时间戳或缺少 GPU 数值的样本显示为新鲜，而调度拒绝这些样本，导致页面和准入提示矛盾。
## What Changes
管理员资源明细复用调度的 GPU 遥测新鲜度判定，不改变样本有效期或调度行为；同步指南和回归。
## Capabilities
### New Capabilities
- `resource-freshness-display`: align displayed GPU freshness with admission
## Impact
GET /api/v1/workers 管理员明细、计算资源指南与测试。
