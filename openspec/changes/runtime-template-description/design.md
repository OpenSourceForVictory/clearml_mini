# Design

## Context

TemplateCreate 被服务端定义继承。客户端配置使用 client_templates 列，服务端使用 definition JSON；template_view/server_view 同时用于 API 和运行快照。模板注册、只读配置、成员草稿编辑以及向导选择有不同入口。

## Goals / Non-Goals

提供可填写、可复制且选择时可见的描述；不引入富文本、不调整权限和已发布版本编辑限制，不把说明写入命令或环境。连接池工作在独立 change 中追踪。

## Decisions

- API 统一 description 字符串默认空，最大 2000 字符，保留换行；拒绝 NUL 防止数据库异常。客户端新增非空列及空串 server_default；服务端沿用 JSON，不另造存储。
- 所有模板 view 输出 description，服务端旧 definition 缺字段时补空。现有客户端快照与服务端快照自然保留字段，历史快照不回填。
- 注册及成员编辑用 textarea；列表不塞完整长文本，只读详情显示。向导在客户端选择框及每个托管服务选择框下放可访问的说明区域，按当前选项派生，不另存易过期的副本。无选择不展示，选中空说明显示空态。
- React 文本节点渲染，white-space: pre-wrap 和 overflow-wrap:anywhere 保留格式且不执行 HTML。保持下拉选项名称/版本简洁。
- 不单独提供绕过修订或所有权的说明编辑接口；已发布模板沿用复制新版本流程。

## Risks / Trade-offs

旧 API 不能识别新字段 → 先迁移并发布 API，再发布前端；统一 API 镜像发布时按原部署迁移顺序执行。旧客户端不传字段正常工作。历史快照缺说明 → 展示空态，不读取最新模板覆盖历史。

## Migration Plan

在 ab852c3 后新增仅添加 description 列的迁移，验证历史行默认空和降级移列；不触碰执行表。回退优先保留新增列回退应用，降级会丢失客户端模板说明。Worker/Runner 无需升级。
