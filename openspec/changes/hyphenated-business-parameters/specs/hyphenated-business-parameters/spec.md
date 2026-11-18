## ADDED Requirements
### Requirement: 原样传递中划线名称
系统 SHALL 接受业务参数和环境变量名中的中划线，并保留名称与值。
#### Scenario: 命令与环境映射
- **WHEN** task-config 绑定 --task-config 或 task-config 环境变量
- **THEN** 业务程序收到原名称和值
#### Scenario: 保留变量
- **WHEN** 参数绑定到平台保留环境变量
- **THEN** 继续拒绝，不因放开中划线绕过保护
#### Scenario: Shell 使用说明
- **WHEN** 用户编辑环境变量
- **THEN** 页面解释中划线名称的 shell 兼容限制
