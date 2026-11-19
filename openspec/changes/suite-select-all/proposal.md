## Why
集合目前只有选择搜索结果的入口，用户需清空搜索才能全选，范围不够明确。

## What Changes
- 增加“选择全部任务”，不受搜索影响。
- 保留“选中当前显示任务”和清空选择，显示选中总数及隐藏选中数。
- 批量参数、回合与移除操作沿用选中集合；已发布清单仍只读。

## Capabilities
### New Capabilities
- `suite-task-selection`: 集合任务选择范围与批量操作可见性。
### Modified Capabilities
无。

## Impact
frontend/src/suites.tsx、集合浏览器测试、界面指南、docs/suite-management.md 与 Skill 引用。无 API、数据库、Worker 变更。
