## Why
机器、模板、集合的详情位于列表底部，长列表打开后难以定位。
## What Changes
- 三类设置改为独立详情视图，提供固定返回入口。
- 返回保留列表筛选、分页和滚动位置；详情表单返回时保留输入，对可能丢失修改的切换提示。
- 同步指南与专项浏览器测试。
## Capabilities
### New Capabilities
- `management-detail-navigation`: 管理列表与详情导航。
### Modified Capabilities
无。
## Impact
仅前端布局、状态和导航；无 API、迁移或 Worker 更新。
