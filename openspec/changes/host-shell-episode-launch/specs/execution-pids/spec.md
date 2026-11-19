## ADDED Requirements

### Requirement: 可选业务进程线程上限
Worker SHALL 对宿主机和Docker使用节点 execution_pids_limit 可选正整数，缺省或null时不添加平台PID上限。

#### Scenario: 默认不额外限制
- **WHEN** 节点未设置上限或设为null
- **THEN** 业务启动显式传TasksMax=infinity或--pids-limit=-1，不检查未申请的PID额度；运行时与系统限制仍生效

#### Scenario: 显式上限
- **WHEN** 节点配置正整数
- **THEN** 宿主机systemd参数、持久化启动配置和两处验证使用相同值，Docker参数使用该值

#### Scenario: 非法配置
- **WHEN** 上限为布尔、字符串、零或负数
- **THEN** 在创建业务运行单元前拒绝并指出配置字段，不静默退回默认

#### Scenario: 探测隔离
- **WHEN** 执行短时环境探测
- **THEN** 原有显式32上限及其校验保持有效
