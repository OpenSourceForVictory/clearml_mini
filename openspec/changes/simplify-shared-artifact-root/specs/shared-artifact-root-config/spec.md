## Purpose
为部署管理员提供统一产物总目录的简化配置，使节点与平台使用同一份存储时无需逐节点手填映射，同时保留既有高级映射、任务授权和目录读取边界。
## ADDED Requirements
### Requirement: Single root configuration
平台 SHALL 支持仅配置 SHARED_ARTIFACT_HOST_ROOT 建立同路径节点产物映射，无需手工marker或节点列表。
#### Scenario: Read registered outputs
- **WHEN** 节点输出在配置总目录内且API已挂载相同文件
- **THEN** 授权用户可以浏览已登记执行目录，领取任务使用共享传输标记。
#### Scenario: Unsafe or absent root
- **WHEN** 总目录为根目录或非法路径，或读取时挂载不存在
- **THEN** 明确拒绝而非创建目录或暴露任意路径。
### Requirement: Advanced configuration precedence
平台 SHALL 优先使用非空高级映射配置并保持历史身份校验与访问控制。
#### Scenario: Existing configuration
- **WHEN** 同时设置总目录和高级映射
- **THEN** 只按高级映射授权；空数组关闭映射，非法JSON报错，不退回简化配置。
#### Scenario: Isolation
- **WHEN** 请求跨任务文件、链接或目录外文件
- **THEN** 即使简化配置适用所有节点也必须拒绝。

### Requirement: Preserve deployment commands
平台 SHALL 在原 deploy/compose.yml 中支持可选产物根配置，无需增加Compose覆盖文件参数，且未配置时保持产物读取关闭。
#### Scenario: Existing deployment without artifacts
- **WHEN** 使用原构建和启动命令且未设置产物总目录
- **THEN** Compose可以正常解析，不要求存在外部产物目录，API不启用共享映射。
#### Scenario: Existing deployment with root
- **WHEN** 设置已有产物总目录并沿用原部署命令
- **THEN** API自动获得只读挂载和对应变量，不影响其他服务；不存在的配置目录不会自动创建。
#### Scenario: Legacy overlay
- **WHEN** 原命令仍包含shared-storage-compose.yml
- **THEN** 相同容器目标挂载合并为一条，显式高级映射仍优先。
