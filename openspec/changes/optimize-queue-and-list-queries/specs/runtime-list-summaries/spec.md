# Runtime List Summaries

## Purpose

为评测批次列表和服务状态提供轻量读取方式，减少用户浏览与轮询时读取未展示的数据，同时确保结果统计、权限、重试尝试、暂停恢复状态与完整详情一致，并保留原接口的兼容行为。

## ADDED Requirements

### Requirement: Compatible batch summaries
系统 SHALL 支持显式请求批次列表摘要，保留原完整列表默认行为。摘要 MUST 包含列表使用的批次标识、状态、控制修订、优先级、进度和两类结果统计，不包含完整任务明细；统计仅使用最新尝试，权限和分页顺序与完整列表一致。

#### Scenario: Summary agrees with detail
- **WHEN** 用户查看包含重试、未上报结果和普通命令任务的批次列表
- **THEN** 摘要统计与同一数据状态下的完整详情一致，进入详情后仍可查看历史尝试

#### Scenario: Existing client compatibility
- **WHEN** 旧调用方未请求摘要模式
- **THEN** 仍返回原有完整列表结构，其他用户和已删除批次不因摘要模式而泄露

### Requirement: Service status without duplicate batch payload
系统 SHALL 提供不重复携带批次任务明细的服务状态视图，包含实例、节点和卡位、就绪状态、关联信息及调度诊断；原编排完整接口 MUST 保持兼容。

#### Scenario: Multiple instances and waiting service
- **WHEN** 用户查看同名多实例服务或尚未分配实例的服务
- **THEN** 每个实例仍按实例 ID 区分并展示位置和状态，尚未分配时保留等待原因

### Requirement: Visible detail subscriptions
详情页 SHALL 只为当前可见的动态内容维持轮询；同一批次的服务概览与服务页不得同时重复轮询相同状态。切换页签 MUST 保留筛选与展开选择，返回可见页面时刷新状态，失败时保留错误提示与重试能力。

#### Scenario: Switch services to tasks
- **WHEN** 用户打开服务页后返回任务页
- **THEN** 隐藏服务页停止轮询，任务页服务概览仅保留一份订阅，返回服务页仍能刷新实例状态

#### Scenario: Logs and artifacts remain on demand
- **WHEN** 用户尚未展开回合、日志或产物
- **THEN** 不因摘要轮询额外读取这些明细；展开后仍按选择的执行尝试读取
