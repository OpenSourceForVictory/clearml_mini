# Proposal
## Why
集合目前只有清单上传、保存和运行，缺少快速选取预置任务、导入预览及版本维护。需要让用户快速建立子集，并保留历史运行的可复现性。
## What Changes
- JSON/YAML 文件及旧清单导入，统一规范化、预览和逐字段校验；自动标识、公共默认值、内容重复提示。
- RoboTwin 固定版本 50 任务预置目录，可选择子集并批量设置。
- 集合草稿、不可变发布版本、复制/新版本、搜索/标签/客户端筛选、导出、归档恢复及未引用草稿删除。
- 保存留在管理页，可显式保存发布并转运行；运行配置仍独立，资产不阻塞导入。
- 迁移现有集合为发布状态；完成回归和正式部署。
## Capabilities
### New Capabilities
- evaluation-suite-management: 可复用集合导入、生命周期和管理界面。
### Modified Capabilities
无。
## Impact
SQLAlchemy/Alembic、集合与批次 API、React CS 界面、预置清单和测试文档。增加服务端安全 YAML 解析依赖；Worker/调度协议不变。
