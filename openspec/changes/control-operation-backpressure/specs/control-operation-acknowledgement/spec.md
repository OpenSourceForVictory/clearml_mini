## Purpose
为恢复服务和重新评测提供与日志资源隔离的控制操作，并明确事务成功确认与详情读取的边界，使用户在高负载下能准确判断操作是否已经生效而避免重复执行。

## ADDED Requirements
### Requirement: 重评控制隔离
平台 SHALL 将单任务与批次重评作为控制请求，日志仍属于普通通道。
#### Scenario: 普通连接池耗尽
- **WHEN** 日志占满普通连接池而控制池可用
- **THEN** 重评请求不因普通池耗尽被拒绝，心跳保持可处理
### Requirement: 服务控制事务确认
平台 SHALL 在服务控制提交成功后返回简短确认，不再读取完整详情；提交失败不能返回成功。
#### Scenario: 详情读取不可用
- **WHEN** 恢复服务成功提交且完整详情查询不可用
- **THEN** 控制操作仍返回 accepted、id、revision、current_job_id 和 action
#### Scenario: 旧版本重复操作
- **WHEN** 用户重复提交旧 expected_revision
- **THEN** 返回冲突，不重复创建服务执行
### Requirement: 有界重评选择
平台 SHALL 仅加载选中任务的历史，保留权限、清理、配额、成员原子检查及执行尝试保护。
#### Scenario: 大批次选择少量任务
- **WHEN** 用户从大批次选取少量失败任务重评
- **THEN** 未选任务不加载、不变更，旧 expected_run_id 重放不会新增尝试
