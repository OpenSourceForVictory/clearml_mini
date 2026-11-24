## Purpose

规定平台已选定资源但 Worker 尚未领取时的预约期限，避免调度事务内的计算耗时提前耗尽领取窗口，并保证未领取资源仍能按原有超时规则回收，不影响运行任务的租约。

## ADDED Requirements

### Requirement: Start new reservation windows near commit
系统 SHALL 在本轮调度计算与主要写入完成后、提交事务前重新计算本轮新预约的期限，保留对应配置时长；本轮服务就绪后开启的领取窗口同样处理。

#### Scenario: Slow scheduling tail
- **WHEN** 新预约建立后本轮计算超过普通预约时长
- **THEN** 提交前按新的计时点设置期限，不将该段计算时间计入领取窗口

#### Scenario: Managed service becomes ready
- **WHEN** 服务就绪使等待客户端获得领取资格
- **THEN** 本轮提交前为它重新计算普通领取窗口，未就绪客户端仍使用服务等待时长

### Requirement: Preserve existing expiry and atomicity
系统 SHALL 保持已有预约的过期回收规则，不因每轮调度自动延长期限；窗口与预约 MUST 原子提交，失败回滚不得污染后续轮次。

#### Scenario: Existing unclaimed reservation
- **WHEN** 旧预约尚未领取且本轮未开启新窗口
- **THEN** 保留原期限，到期后按既有规则重新排队

#### Scenario: Transaction fails
- **WHEN** 新窗口写入或预约事务提交失败
- **THEN** 不留下新预约或延长窗口，下轮重新计算
