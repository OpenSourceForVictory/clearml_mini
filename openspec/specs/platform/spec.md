# Evaluation Platform

## Purpose
管理多用户评测请求，并按物理 GPU 卡安全分配执行槽位。

## Requirements

### Requirement: 异构 GPU 槽位
系统 SHALL 允许每台机器注册一张或多张 GPU，每张卡有唯一队列。

#### Scenario: 多卡机器
- **WHEN** 管理员注册同一机器的两张不同 GPU
- **THEN** 系统为每张 GPU 保存独立槽位和队列

### Requirement: 提交先入库
系统 SHALL 将任务保存到数据库，由调度器决定何时投递。

#### Scenario: 用户提交任务
- **WHEN** 用户提交有效评测请求
- **THEN** 系统保存 PENDING 任务和首次尝试

### Requirement: 可恢复投递
系统 SHALL 使用稳定任务标记对账，避免崩溃后重复投递。

#### Scenario: 投递期间重启
- **WHEN** 调度器在创建 ClearML Task 后重启
- **THEN** 调度器按任务标记恢复原 Task
