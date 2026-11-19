## Context
当前 suites.tsx 用原始任务索引维护 selected，visible 仅按搜索过滤。

## Decisions
全选从 doc.cases 原始索引构造集合；保留现有当前显示选择的替换语义，不改成累加。显示 selected.size 及不在 visible 中的选中数，明确批量操作影响范围。复用现有 locked/busy 禁用逻辑。无需接口或迁移。

## Risks / Trade-offs
隐藏任务可能被批量修改 → 选中总数与隐藏数量始终显式显示，并在指南区分两个入口。只做前端改动，部署可随前端回滚。
