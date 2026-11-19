# API 共享存储直读产物

运行结果 → 任务/回合 → **查看产物**，选择“共享存储（API直读）”。API 从管理员配置的共享挂载读取本次执行的 output 目录，可逐层浏览、预览 JSON/TXT/CSV、图片和视频、下载文件。文件不复制进数据库，不经 Worker 转发；节点离线但共享存储仍可访问时也能读取。

本次只支持平台输出目录。自定义浏览目录、Worker 文件转发、SDK 自动目录归属 manifest 暂未启用。回合筛选依据协议 artifacts 相对路径引用，作用于当前目录页；目录始终可进入。业务未声明回合归属时显示“未声明”，不凭文件名推测回合。

## 1. 准备已有共享挂载

节点工作目录必须位于共享存储内；平台服务器也须挂载同一存储。例：

- 节点名（与机器管理中的名称完全一致）：node-a
- 节点共享根：/efs/project
- 平台服务器已有挂载：/srv/eval-shared
- API 容器内只读挂载：/mnt/mini-eval-artifacts

节点输出 /efs/project/node-a/gpu0/runs/执行ID/output 对应 API 内 /mnt/mini-eval-artifacts/node-a/gpu0/runs/执行ID/output。映射按路径组件匹配，不是字符串前缀替换。相同节点、同一路径必须只有一个匹配项；不要配置重叠根。

在确认是正确共享盘之后，由存储管理员**一次性**建立根目录标识文件（示例值不是密码；每个存储使用稳定的不同标识，已存在时先核对，不覆盖）：

```bash
test -d /srv/eval-shared
# 先使用 findmnt -T /srv/eval-shared 核对来源，不能在未挂载的本地空目录初始化标识。
test ! -e /srv/eval-shared/.mini-eval-storage-id &&
  (set -C; printf '%s\n' 'eval-shared-project-001' > /srv/eval-shared/.mini-eval-storage-id)
```

节点业务账号负责写文件；API 容器账号需要沿途目录搜索权限及文件读取权限。通过现有组/ACL授予读取权限，不要递归改成全员可写。API 不切换为业务账号、不执行登录 shell。只读挂载不会绕过 NFS root_squash 或文件权限。

## 2. 配置与升级

在原部署环境文件中增加（JSON 单引号是 Compose env-file 的外层引号）：

```dotenv
SHARED_ARTIFACT_HOST_ROOT=/srv/eval-shared
MINI_EVAL_SHARED_STORAGE='[{"id":"project","node_names":["node-a"],"node_root":"/efs/project","api_root":"/mnt/mini-eval-artifacts","marker":"eval-shared-project-001"}]'
```

多节点访问同一根可添加 node_names；不同节点根路径或不同存储使用不同 id 的映射项。多存储挂载自行扩展 overlay，全部使用 read_only: true 和 create_host_path: false。该环境变量必须进入 API 容器，仅修改 .env 而不重建容器不会生效。

沿用原项目名、原环境文件及原有全部 overlay，再追加：

```bash
DC=(docker compose --env-file .env -p mini-eval -f deploy/compose.yml \
  -f deploy/shared-storage-compose.yml)
```

如果原部署使用 lan-compose.yml 或固定镜像版本文件，保留这些文件及其顺序。按[数据库迁移指南](database-migration.md)构建同版 API/migrate/bootstrap、排空任务、备份并升级到 **a730d12**，随后用这个 DC 数组启动。以后重建 API 也须保留共享 overlay。无需新增文件存储卷，不要执行 down -v。

更新相应节点到 **Worker 0.21.9**。当 API 对该节点下发 artifact_transport=shared 时，新 Worker 只登记输出路径，不再主动上传产物正文；未配置映射的节点沿用旧逻辑。旧 Worker 会忽略新标记、继续旧上传行为，因此仅升级 API 不能保证停止上传。Runner/SDK 不需要为了直读而升级。

活动任务保存了旧下发配置，升级后的新任务才保证采用新行为。API 先升级并迁移，再更新节点。移除映射只会撤销读取授权，不会自动转存或删除文件。

## 3. 目录身份与历史兼容

新执行由 Worker 在启动及完成阶段幂等登记 runs/<execution_id>/output；登记失败写 Worker warning，不能改变业务执行结果。历史执行可使用该执行已上报的 ExecutionData 实际目录登记；没有记录或存在歧义时明确提示“执行输出目录未上报”，不从当前工作目录设置推断旧位置。

新表 shared_artifact_roots 只保存执行与目录映射元数据，不存文件正文。已登记执行不可重新绑定目录；API 本地挂载位置可以变化，但存储 id、marker、相对路径保持身份约束。修改 marker 或撤销节点授权会拒绝访问。删除批次会删除登记与旧数据库产物，**不会删除共享盘上的文件**；节点原有保留/清理策略仍适用。

未启用共享存储时自动显示“历史上传产物”；已启用但挂载、权限或目录出错时明确报错，不伪装空目录或自动隐藏到历史页。仍可手动选择历史页查看旧 RunArtifact 正文。

## 4. 读取边界及错误

- 每页最多100条，下一页带目录版本；目录变化需从首页刷新。
- 按需1 MiB分块读取，不加载整段视频；支持单一 Range、HEAD、ETag/If-Range。文本预览最多256 KiB，下载不限文件总大小。
- 鉴权和目录元数据查询后立即释放数据库连接，再启动隔离子进程读共享盘。每次 I/O 超时10秒；每个 API 进程最多8个 I/O、每用户2个；媒体响应另有相同数量的全程并发限制。多进程部署上限按进程累加。
- 读取超时杀子进程；处于不可中断 NFS 等待的子进程在实际退出前仍占额度。流中途文件变化/存储失败会中断传输，浏览器可刷新后重试，不拼接不同版本。
- 媒体授权使用5分钟以内、且不超过登录会话的文件限定 HttpOnly cookie；URL不携带登录 token。正在下载跨过授权截止时间会停止，重新授权后可继续范围读取。浏览器不支持视频编码时可下载。
- 拒绝路径穿越、符号链接、多硬链接及非普通文件；HTML/SVG只下载，不执行。
- 403：文件权限不足或不安全路径；409：版本变化/映射冲突/目录未登记；410：文件或目录已清理；429：读取繁忙；503：共享挂载缺失、标识不符或无权读取根标识；504：共享存储超时。

只读 overlay 防止 API 写共享盘；根标识用于识别错挂载，不是认证秘密，也不是文件备份。API 能看到的范围取决于管理员映射及任务归属授权。不要把密钥输出到产物目录。
