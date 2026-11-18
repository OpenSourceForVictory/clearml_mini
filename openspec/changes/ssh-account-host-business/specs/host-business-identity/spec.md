# 宿主机业务身份

## ADDED Requirements

### Requirement: 新纳管默认使用 SSH 账号

新 root 系统安装 MUST 将 root SSH 纳管账号作为批准的业务账号，使用 UID/GID 0。已有安装 MUST 保留原业务账号。

#### Scenario: root 纳管

- **WHEN** 管理员使用 root SSH 纳管新节点并选择宿主机执行
- **THEN** 每张卡的宿主机配置使用 root，业务进程以 UID 0 启动

#### Scenario: 已有普通账号重新纳管

- **WHEN** 管理员重新纳管使用普通业务账号的已有节点
- **THEN** 宿主机配置继续使用原批准账号，业务环境从该账号的登录 Shell 读取

#### Scenario: 容器模式

- **WHEN** 节点只选择容器执行
- **THEN** 继续使用镜像和容器运行配置中的用户，不改变容器身份

### Requirement: 新纳管按机器和设备创建目录

新纳管 MUST 允许配置工作目录根路径，并为每张设备使用 `根路径/机器名/设备类型及编号` 作为实际工作目录。安装器 MUST 创建不存在的工作、资产和缓存目录，已有安装 MUST 保留原目录。

#### Scenario: 多卡节点

- **WHEN** 管理员配置 `/data/eval` 并纳管名为 `node-a` 的 GPU 0 和 GPU 1
- **THEN** 安装计划和本地配置分别使用 `/data/eval/node-a/gpu0` 与 `/data/eval/node-a/gpu1` 作为工作目录，两卡均使用 `/data/eval/node-a/assets` 和 `/data/eval/node-a/cache` 作为共享资产与缓存目录

#### Scenario: 不同机器

- **WHEN** 两台机器使用同一个工作目录根路径
- **THEN** 各机器的资产和缓存目录由机器名区分，不自动跨机器共享

#### Scenario: 目录已存在

- **WHEN** 目标目录已有业务文件
- **THEN** 安装器保留原文件和属主，仅创建缺失的目录
