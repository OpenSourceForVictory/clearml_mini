## ADDED Requirements
### Requirement: Installed CANN environment for NPU host tasks
NPU 宿主机 Worker SHALL 在批准的业务身份下加载本机已安装的 CANN 运行环境，并将必要的 CANN 变量传给业务进程，而不继承 root Worker 或交互登录 shell 的完整环境。

#### Scenario: Standard CANN installation
- **WHEN** NPU 节点具有可用的标准 CANN `set_env.sh`，模板启动宿主机服务
- **THEN** 业务进程获得 CANN 库搜索路径，模板的业务库目录仍保留，设备可见性仍由平台设置

#### Scenario: Installed setup fails
- **WHEN** 已发现的 CANN 脚本不能以业务账号运行或不提供库搜索路径
- **THEN** 宿主机环境报告不可用，业务启动前给出初始化失败原因

#### Scenario: No standard setup script
- **WHEN** 节点没有可发现的 CANN 脚本
- **THEN** 旧节点已批准的显式 CANN 环境继续生效，Worker 不从 root 或登录 shell 隐式继承变量

### Requirement: Backend capability uses template execution mode
调度器 SHALL 按任务模板的宿主机或容器执行方式匹配 NPU 与共享 GPU 的节点后端能力。

#### Scenario: NPU host backend
- **WHEN** NPU 模板要求宿主机执行，节点仅上报 NPU 容器后端
- **THEN** 调度拒绝该设备；节点上报匹配的 NPU 宿主机后端后允许继续资源检查
