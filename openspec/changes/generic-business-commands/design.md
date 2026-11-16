## Context
命令通过 subprocess 执行，客户端、服务端及逐回合包装有独立入口。旧宿主机依赖 Python 脚本路径转换。
## Goals / Non-Goals
支持普通命令、Bash/Python/原生可执行文件及显式 Shell；不提供不可信代码安全沙箱，不新增编译/依赖安装。
## Decisions
host_runtime/runtime 增加 command_mode=exec（缺省保留 legacy）；新界面默认 exec。exec 宿主机缺省 cwd 为批准代码根目录，显式目录仍受批准根限制。Python 别名沿用节点 Python，其他程序按绝对路径、cwd 相对路径、最终 PATH 查找。不猜测解释器后的参数，全部原样传递。平台宿主机入口使用 Worker Python，业务使用指定程序。Docker 保留镜像提供平台 Python 的要求，但允许其绝对路径，业务程序与平台解释器分离；支持独立容器 working_directory。最终程序检查由业务账号运行的公共 resolver 完成。协议 14 防止旧 Worker 接收新字段。
## Risks / Trade-offs
exec 允许运行账号可执行的系统程序，属于可信业务执行；权限、systemd/cgroup/容器限额保持。旧模板不自动迁移。Docker 镜像仍需平台 Python 和业务依赖，非 Python 业务无需 SDK。
## Migration Plan
升级 API/Worker 后新模板可选择 exec，旧模板复制后显式切换；本次不自动升级节点或发布。
## Open Questions
无。
