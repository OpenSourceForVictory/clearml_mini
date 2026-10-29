# Spec Delta

## MODIFIED Requirements

### Requirement: 异构 GPU 槽位
系统 SHALL 允许每台机器注册一张或多张 GPU，每张卡有唯一队列。同一物理资源池 SHALL 接纳不同型号的 GPU，并记录每卡真实型号与总显存；任务可选指定型号及最低显存，调度器只选择符合要求的设备。虚拟模拟设备与物理 GPU SHALL 分池管理。

#### Scenario: 多卡机器
- **WHEN** 管理员注册同一机器的两张不同 GPU
- **THEN** 系统为每张 GPU 保存独立槽位和队列

#### Scenario: 不同型号共池
- **WHEN** RTX 8000 和 RTX 4090 注册到同一物理资源池且满足池最低显存
- **THEN** 两张卡均可执行该池不限定型号的合格任务

#### Scenario: 限定设备需求
- **WHEN** 用户指定某 GPU 型号或所需显存超过部分设备容量
- **THEN** 任务只会分配给满足条件的设备，无合格设备时保留排队
