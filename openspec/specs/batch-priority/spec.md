# Batch Priority Specification

## Purpose

本能力允许用户为仍有排队任务的评测批次设置优先级，帮助控制同一用户的调度顺序，并保持跨用户公平轮转、相同优先级的 FIFO 行为及已运行任务不被抢占。

## Requirements

### Requirement: Batch priority ordering
系统 MUST 为每个评测批次保存 0–9 的优先级，默认值为 0，较大值优先。数据库升级前创建的批次 MUST 迁移为 0。

#### Scenario: Default priority uses FIFO
- **GIVEN** 同一用户有多个仍排队且优先级相同的批次
- **WHEN** 调度器尝试分配任务
- **THEN** 较早创建的批次先参与调度，批次内任务按原有顺序处理

#### Scenario: Higher-priority batch is considered first
- **GIVEN** 同一用户有两个兼容资源的待调度批次，后创建批次优先级更高
- **WHEN** 调度器尝试分配任务
- **THEN** 先尝试较高优先级批次的任务
- **AND** 不撤销或抢占已预留、启动或运行的任务

#### Scenario: Cross-user fairness is preserved
- **GIVEN** 多个用户均有待调度任务
- **WHEN** 批次优先级不同时调度
- **THEN** 用户间仍按现有公平轮转进行，批次优先级只改变该用户内部批次顺序

### Requirement: Queued batch priority control
系统 MUST 允许批次所有者或管理员调整仍有 `PENDING` 任务的批次优先级。系统 MUST 拒绝越界值、无排队任务的批次修改、无权限访问及过期修订号，并返回清晰错误；拒绝时不得修改优先级。

#### Scenario: Owner updates a queued batch
- **GIVEN** 用户拥有一个仍有排队任务的批次
- **WHEN** 用户提交有效优先级和当前修订号
- **THEN** 系统保存优先级、推进修订号并返回更新后的批次视图

#### Scenario: Batch has no queued tasks
- **GIVEN** 批次任务全部已运行或结束
- **WHEN** 用户修改优先级
- **THEN** 系统返回冲突错误且原值不变

#### Scenario: Another user attempts an update
- **GIVEN** 批次不属于当前用户
- **WHEN** 非管理员尝试修改
- **THEN** 系统返回 404 且不泄露批次信息
