# Proposal

## Why

实际浏览器核查发现五步向导的配置层次、参数来源和最终确认不够清楚，用户可能遗漏服务端资源池或误解回合、共享及预检状态。已有23项交互回归通过，但未覆盖这些信息展示缺口。

## What Changes

- 保留五步流程，按任务、客户端、服务端、资源与并发、检查与提交组织模块。
- 任务摘要与单项详细配置分层；已保存集合明确只读与复制修改入口。
- 展示客户端运行方式、接入协议、回合启动和失败处理策略；参数来源与覆盖关系可见。
- 服务卡片直接展示实例数、资源池、服务访问方式；环境变量采用允许键的键值编辑并保留高级JSON。
- 完整确认页、按模块返回修改、错误定位和草稿保留；区分配置校验、资源、环境及实际连通性。
- 修复复选框布局、窄屏及长页面操作；同步用户指南和项目Skill引用说明。

## Capabilities

### New Capabilities

- `evaluation-wizard`: 评测提交向导的分组展示、草稿导航、配置确认与诊断交互。

### Modified Capabilities

无。现有platform规范不包含向导交互要求；关联batch-rerun-wizard、batch-run-configuration变更中的历史配置语义保持兼容。

## Impact

主要涉及frontend/src/run-wizard.tsx、managed-services.tsx、business-values.tsx、resource-requirements.tsx、style.css、guide-content.ts及对应浏览器测试。复用现有预检和提交API，不改资源调度、参数优先级、幂等提交或执行协议；不需要数据库迁移、Worker/Runner升级。批次结果页本轮不重构，但术语与配置分组对齐。
