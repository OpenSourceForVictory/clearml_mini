## Purpose
为宿主机逐回合启动的独立业务程序提供明确的错误处理策略，使用户能够选择遇到执行错误后立即停止或继续完成剩余回合，同时保留真实退出码与任务失败语义，避免把执行异常误报为业务成功。
## ADDED Requirements
### Requirement: 可选继续策略
客户端宿主机逐回合模板 SHALL 支持 stop/continue 错误策略，遗漏时为 stop。
#### Scenario: 首回合退出非零
- **WHEN** 三回合任务选择 continue 且第一回合退出1
- **THEN** 第一回合记录ERROR及退出码1，第二第三回合仍执行，任务最终失败
#### Scenario: 默认停止
- **WHEN** 未配置策略且回合发生执行错误
- **THEN** 立即停止后续回合
#### Scenario: 结果缺失
- **WHEN** continue任务进程退出0但协议结果缺失或非法
- **THEN** 记录ERROR及退出码0，继续后续回合且最终失败
### Requirement: 生命周期与兼容性
系统 SHALL 保留正常业务失败、取消、总时限及租约语义，且拒绝旧Worker执行continue模板。
#### Scenario: 正常业务失败
- **WHEN** 回合正常完成且协议success为false
- **THEN** 记录COMPLETED及业务失败，继续后续回合
#### Scenario: 取消或启动故障
- **WHEN** 任务取消、租约失效或初始化失败
- **THEN** 不因continue而继续创建业务程序
#### Scenario: 旧Worker
- **WHEN** continue模板遇到协议16及以下Worker
- **THEN** 阻止领取并提示升级
#### Scenario: Docker与整任务
- **WHEN** 使用Docker或宿主机whole_task模板
- **THEN** 保持原执行行为，不允许配置宿主机逐回合continue策略
