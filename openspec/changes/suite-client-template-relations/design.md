## Decisions
使用关联表（集合 ID、模板 ID 联合主键），迁移旧 template_id，保留原列作为默认关联；template_ids 为 API 列表。至少关联一个模板，默认必须在关联内；旧单模板请求自动转换。ORM 使用 selectin 加载，历史手工记录可回退默认。
已有已发布集合可用独立、owner scoped、expected_revision 的 PUT client-templates 修改关系，清单内容仍只读。校验模板已发布/启用/可见，同任务类别，每个模板的业务参数 schema 都能接受集合默认值和每个任务。模板参数映射继续由各模板管理；不自动宣称仿真任务名已经实现。资源和服务兼容性在运行预检继续校验。
运行绑定一个具体模板版本并保留现有快照，移除关联不影响历史或运行中任务。提交时检查最新关系，避免 stale UI 绕过。导出增加 clients 名称/版本列表，保留 client 默认提示；导入未匹配项明确列出并要求确认选择。
## Risks
已停用关联仍显示不可用，允许移除；不能继续新增运行。清空所有关联拒绝。已发布集合只改关联，不能借此改清单。关系变化沿用 suite revision 防并发覆盖。数据库迁移前备份，先验证迁移再上线，无 Worker 升级。
## Validation
API 权限、修订冲突、不可见模板、参数不兼容、双向复用、导入导出/复制、运行选择与移除后历史快照、旧请求兼容。UI 已发布增减关联、向导筛选/切换、失败保留草稿。关联回归和迁移检查。

## UI scope clarification
Collection management is the only entry for editing available client templates. Use checkboxes and a save action on existing collections; no separate relationship manager or reverse template editor. The wizard selects one allowed template.
