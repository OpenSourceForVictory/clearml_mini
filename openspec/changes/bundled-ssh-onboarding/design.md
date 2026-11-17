## Architecture
独立轻量镜像只包含 Paramiko、执行器和所需安装/探测代码，不持有数据库或 JWT 凭据，不挂 Docker socket，不开放 TCP 端口。API 与执行器共享专用 Unix socket volume；只有执行器挂载操作记录 volume。API 默认 socket 指向此卷，旧 ONBOARDING_SOCKET 与宿主机目录挂载继续生效。执行器回调 http://api:8000，无需主机端口参数。健康检查只检查本地执行器，不依赖 API，避免依赖环。
## Recovery
unless-stopped 负责进程退出恢复。重启保留脱敏操作记录，原临时凭据失效、活动操作按现有 INTERRUPTED 处理，不自动重放安装。用户在界面重新检查。两个执行器使用不同目录和锁，不抢旧 socket。
## UI
成功读取列表显示就绪；失败立即置不可用并显示重连提示，保留输入、既有操作记录和业务错误。轮询恢复后清除连接提示，提供重新检测按钮。不声称界面可修复 Docker 守护进程故障。
## Validation
配置契约、真实 Unix socket 健康检查、异常恢复、UI 失联/重连、隔离 Compose 启动/重启和真实 SSH 主机指纹检查；不对真实节点执行安装。
