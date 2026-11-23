# Proposal

## Why

客户端和服务端模板目前只有名称与版本，用户在评测向导选择模板时无法了解用途、依赖和注意事项。新增用户填写的说明，帮助选择正确模板。

## What Changes

- 两种运行模板新增可选多行纯文本 description，最多 2000 字符，缺省为空。
- 注册、草稿编辑、复制新版本、只读查看完整保留说明。
- 向导选中客户端或每个托管服务模板后，就近显示说明；切换模板同步切换，无说明给出明确空态。
- 历史任务保留快照，不用新说明覆盖旧记录；说明不参与命令拼接、环境注入或调度。

## Capabilities

### New Capabilities

- `runtime-template-description`: 运行模板的用户说明及选用时展示。

### Modified Capabilities

无。

## Impact

ClientTemplate 表、模板定义/API/快照，模板创建与成员草稿编辑界面，评测向导客户端和托管服务选择；新增兼容数据库迁移。仅 API/前端更新，不要求更新 Worker/Runner。同步使用指南、数据库迁移说明和 Skill 索引。
