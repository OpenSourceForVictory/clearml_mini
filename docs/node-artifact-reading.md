# 节点产物读取：内部接口与实施边界

当前阶段提供 Worker 侧只读基础模块，尚未接入运行结果页面或 Worker 主循环；不能把本文件当作节点浏览已上线的说明。页面现有预览仍使用旧产物接口。用户确认的最终方向是文件保留在节点，仅登记索引、按需读取，**不主动上传文件或新增集中产物卷**。

## 读取身份与授权

`worker/artifact_access.py` 提供 `register(root, approved_roots, account, revision)`，供可信的执行启动逻辑登记批准根内目录、目录设备号/inode、业务账号及批准版本。它不持久保存注册；注册持久化和执行/节点/slot 绑定属于后续接线工作。不能直接使用浏览器给出的绝对路径或账号调用注册。

`read(registration, request, approval_revision=...)` 必须传入节点**当前**批准版本。批准变化、账号删除、UID/GID、补充组、home 或 shell 变化会拒绝读取。Worker 是 root 时通过子进程降到已登记账号；Worker 不是 root 时只允许其当前身份及组完全匹配。没有 root 回退。

读取子进程使用 Worker 的 Python、`-I` 隔离启动和固定最小环境，不执行登录 shell，不继承业务的 PATH/PYTHONPATH/代理变量。子进程必须能访问平台 Python 与 helper 文件，否则返回 `reader_unavailable`；不自动放宽权限。

## 有界只读请求

- `list`：`path` 为根内相对目录（空字符串表示根），`limit` 为 1–100。返回名称、类型、大小、文件版本及分页位置。下一页同时传上页的 `next`（作为 `after`）和目录 `version`。目录变化后重新从首页读取，不能继续拼旧页。
- `stat`：相对文件 `path`，返回 `size_bytes`、`version`。只允许业务账号可读的普通文件。
- `read`：必须带此前获得的文件 `version`，`offset` 从 0 到文件长度，`length` 为 1–1048576 字节。返回块的 base64 字节。base64 仅用于本机父/子进程通信，不允许将正文存入数据库。后续网络通道应转成有界二进制流。

只读 helper 为 `worker/artifact_reader.py`。它逐层使用 fd/no-follow 校验；父目录只要求穿越权限，列目录才要求读取权限。最终文件先以 O_PATH 固定 inode、确认普通文件，再经 `/proc/self/fd` 打开同一 inode，避免先打开设备或管道。拒绝软链接、多硬链接文件、设备、FIFO 和 socket；平台协议私有目录不可请求。

单次子进程默认 10 秒超时；输入最大 16 KiB，范围最多 1 MiB，列表最多 100 项。分页扫描为保证文件名排序会遍历目录，但只保留有限条目；超大/慢目录通过子进程超时明确失败。目录版本包含时间戳及名称/inode 摘要，避免仅靠时间戳漏判快速创建/删除。文件版本包含设备号、inode、大小、mtime、ctime；它不是内容 hash，不是文件备份。

错误以稳定状态返回，不把失败展示为空目录：`missing`、`permission_denied`、`unsafe_path`、`unsupported_file`、`root_changed`、`file_changed`、`approval_changed`、`account_missing`、`account_changed`、`timeout`、`reader_unavailable`、`invalid_path`、`invalid_request`、`io_error`。节点离线由尚待接入的通道层判断，helper 不伪造在线状态。

## 容器与剩余接线

现有 Worker 对 `cs-client` / `inference-service` 已将 `runs/<execution>/output` 挂载进容器（包括配置的 output_dir 别名），容器删除不会自动删除节点输出，不需另建集中复制流程。其他老式执行不能据此推断已有该挂载。

本阶段真实 Docker 验证使用与宿主账号相同的显式 UID/GID，生成 2 MiB 样例文件、删除容器后分块读取并核对 SHA-256。**这不等同任意镜像身份已支持**：后续接线仍需登记镜像实际 UID/GID、处理容器与宿主权限边界和批准版本，不能默认把容器输出交给 root helper 读取。

仍待完成：目录注册持久化、API 元数据表与迁移、节点请求通道、容量/取消/离线处理、新 Worker 停止上传的能力协商、模板自定义目录、回合归属、界面节点浏览和真机批次验收。本阶段无数据库迁移、未部署或升级节点。历史数据库产物读取保持原行为。

验证记录见 [节点读取基础验证](validation/node-artifact-reader-20261119/README.md)。
