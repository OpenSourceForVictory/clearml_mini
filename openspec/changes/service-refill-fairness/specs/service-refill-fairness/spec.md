## ADDED Requirements

### Requirement: Fair service refill
系统 SHALL 在客户端准入后的补齐阶段按用户持久轮转，每用户每轮最多成功补一个实例；同用户 SHALL 按批次优先级降序、创建时间升序、ID 升序尝试。

#### Scenario: One remaining allocation
- **WHEN** 同用户两个批次都需要补实例且仅剩一个位置
- **THEN** 满足资源条件的高优先级批次先获得位置，同级较早批次先获得

#### Scenario: Rotation across ticks
- **WHEN** 上一轮只为某用户补齐且其他用户仍可补齐
- **THEN** 下一轮从后继用户开始，不因优先级跨越用户轮转

### Requirement: Preserve allocation invariants
系统 SHALL 保持既有资源和联合容量检查，不满足的候选不能阻塞其他可满足候选；游标与预约 MUST 同事务提交或回滚。

#### Scenario: Ineligible head
- **WHEN** 排在前面的批次暂停或资源无法满足
- **THEN** 尝试后续候选，不提前释放现有执行资源

#### Scenario: Rollback
- **WHEN** 事务失败
- **THEN** 新实例与轮转游标均不持久化
