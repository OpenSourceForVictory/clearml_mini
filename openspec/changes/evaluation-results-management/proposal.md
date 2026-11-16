# Why
评测运行与结果的批次垂直堆叠难以浏览，也缺少历史批次删除入口。
# What Changes
结构化批次卡片、当前页概览、当前页搜索和状态筛选、比较工具栏、进度和响应式布局。增加安全的逻辑删除接口及确认流程。
# Capabilities
## New Capabilities
- `evaluation-results`: 批次浏览和安全删除。
## Modified Capabilities
无。
# Impact
前端批次列表、后端批次读取及补跑保护、数据库新增可空删除时间列。调度协议不变。
