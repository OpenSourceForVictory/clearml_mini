## Purpose

将可复用运行定义与机器目录、具体任务输入和运行实例分离，为不同评测客户端及推理服务提供统一版本管理，支持容器和宿主机两种环境而不硬编码仿真业务。

## ADDED Requirements

### Requirement: Separate template registration and browsing
运行模板管理 SHALL 在客户端/服务端分类下提供已注册模板和注册新模板两个页签；切换页签 MUST 保留未提交草稿，复制新版本 SHALL 进入注册表单，替换已有草稿前须让用户确认。

#### Scenario: Browse while drafting
- **WHEN** 用户填写模板后切换到已注册模板，再返回注册页签
- **THEN** 原有字段、业务参数和挂载草稿保留，列表中不混排注册表单

#### Scenario: Successful registration
- **WHEN** 注册成功
- **THEN** 返回对应类型的已注册模板页签并刷新列表；注册失败则保留草稿和错误提示

#### Scenario: Copy with existing draft
- **WHEN** 用户在已有未提交草稿时点击复制新版本
- **THEN** 先确认是否替换，取消不丢失草稿，确认后带入完整源配置且要求填写新版本

### Requirement: Contextual template guidance
注册界面 SHALL 提供与模板类型、执行方式和接入方式匹配的操作说明及填写示例，解释平台、业务、运行参数的来源和传递方式；浏览帮助 MUST 不改动草稿或提交模板。

#### Scenario: First client template
- **WHEN** 用户打开客户端注册并选择 SDK 命令模式
- **THEN** 展示命令数组、平台占位符、业务参数绑定和每回合结果文件说明，明确脚本及镜像需自行准备

#### Scenario: Server and host guidance
- **WHEN** 用户切换至服务端或宿主机模板
- **THEN** 帮助展示对应的端口健康检查或批准环境与目录要求，不将客户端逐回合上报描述为服务端必需条件

#### Scenario: Existing draft
- **WHEN** 用户展开或关闭帮助示例
- **THEN** 原有命令、参数及挂载草稿不变，没有写 API 请求

### Requirement: Structured read-only template configuration
查看配置 SHALL 使用与注册一致的分组和字段含义展示实际配置，提供关闭、复制新版本和折叠原始 JSON；查看 MUST 为只读，且不得因展示转换丢失复制源数据。

#### Scenario: Typed container template
- **WHEN** 查看带类型化业务字段、环境变量和挂载的 Docker 模板
- **THEN** 展示字段类型、约束、默认值、绑定、覆盖权限和挂载来源/目标/权限，保留补充配置与原始 JSON 的访问

#### Scenario: Host server template
- **WHEN** 查看宿主机服务端模板
- **THEN** 展示批准环境名称、命令、监听端口与健康检查，不将其误显示为 Docker 运行

#### Scenario: View then copy
- **WHEN** 用户查看后选择复制新版本
- **THEN** 打开基于完整原模板的编辑表单，原版本不变；仅查看与关闭不会发出写请求
