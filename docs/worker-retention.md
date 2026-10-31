# Worker 执行数据与保留策略（v6）

管理员在“管理设置 → 机器名 执行数据”请求扫描，选择条目，预览后确认清理。
普通用户没有宿主目录管理权限，在自己的任务详情中可查看本地数据是否已清理；平台已上传产物、日志和指标继续保留。

## 范围和容量口径

只纳管 Worker 已实际使用、已持久记录身份的 `workspace_root/runs/<execution UUID>`。
根历史存储在 `<state_path>.retention/roots.json`，目录切换前保存旧根，恢复 journal 的路径也登记。
不搜索升级前未记录的其他磁盘目录。未知 UUID 必须匹配本 slot 的服务端 Run 才能清理；探针和非 UUID 目录只显示保护原因。
公共 assets/cache、镜像、凭据和恢复 journal 不属于清理范围。

后台扫描每轮最多 1000 个条目或 200ms（单次系统调用不保证硬实时），轮间让出 CPU，独立于心跳和执行线程。
扫描按目录产生部分/完整状态，可以继续未结束的遍历；进程退出后重新扫描，未完成报告不会变为零。
每条报告包含目录 inode/device、树元数据指纹及 generation。分页默认 50、最多 100 条。
逻辑字节和分配字节包含目录元数据，同目录硬链接去重；稀疏文件的两个值会不同。
跨目录硬链接、其他进程的磁盘写入会影响实际回收。回执的可用空间前后值仅是文件系统观测，不是“精确释放量”。

## 预览与异步清理

1. `GET /api/v1/admin/nodes/{worker_id}/execution-data?offset=0&limit=50` 获取盘点、扫描请求版本、槽位能力和策略。
2. `POST .../execution-data/scan` 请求后台扫描，202 只表示请求已保存。
3. `POST .../execution-data/preview`，请求示例：
   ```json
   {"entries":[{"id":"盘点ID","generation":"读取到的版本"}]}
   ```
   最多 100 项；返回冻结的目录身份、估算与逐项保护原因。
4. 十分钟内 `POST .../execution-data/operations/{preview_id}/confirm`。只确认该预览；重复确认返回同一操作。
5. `GET .../execution-data/operations?offset=0&limit=20` 查看逐项 queued/running/succeeded/skipped/failed 与回执。

API 202 不表示删除成功。完整盘点超过 24 小时、离线/旧 Worker、未结束任务、未完成容器清理、配置已变化等均不能开始新清理。
服务端终态并不代表本地已收尾：Worker 再检查活动线程、恢复 journal 和所有 Docker 容器（包括已停止容器）的执行标签与目录挂载引用。
Docker 查询失败时拒绝清理，不会自动 kill、rm 或 prune 被引用的容器。

失败项先请求扫描，重新选择、预览、确认。部分删除失败的原目录保留在暂存区，扫描将其作为同一执行的暂存数据展示；新预览只绑定现存身份和内容。
原操作的回执保持不变，重试产生新的审计项。不会通过重放旧回执删除同名替代目录。

## Worker 协议及安全边界

`worker/self` 宣告 `retention_v6`；注册使用 `protocol_version: 6`。v1–v5 仍可运行原有评测，不具备清理能力。
以 Worker 凭据调用本 slot 下的：

- `POST worker/slots/{slot_id}/execution-data/inventory`，`entries` 最多 100 项。字段见 OpenAPI `Inventory`/`Entry`；只完整报告可替换完整估算。
- `POST .../control`：取得扫描请求版本以及最多 10 个 queued/running 项。同一操作重复投递，不能转交其他 slot。
- `POST .../{item_id}/begin`：开始授权，30 秒有效；检查终态、归属、generation、storage revision 及自动策略版本。
- `POST .../{item_id}/receipt`：持久化的终态回执；相同内容可重放，不同内容返回冲突。

管理员路径不会作为删除命令被 Worker 打开；Worker 只从自己的根历史解析 opaque root ID。
仅支持有 `/proc/self/fdinfo` mount ID 的 Linux。所有路径组件、根及目录以 no-follow fd 打开，检查 inode/device 和 mount ID。
同盘 bind mount（包括文件挂载）也拒绝跨越。目录内 symlink 只解除链接，不跟随目标。
每个可信根内 `.retention-locks/<UUID>` 的 flock 防止多进程接管同一执行；与本 supervisor 的启动互斥同时检查本地执行状态。
本机制不是对抗宿主 root 管理员的安全沙箱；不要把恢复状态和暂存目录挂载给客户端容器。

## 恢复与故障处理

`<state_path>.retention/item-<operation-item-ID>.json` 保存 intent、staged、deleting、empty、removed 阶段和回执。
先 fsync intent，再将原目录原子 rename 到 `runs/.cleanup/<UUID>`；暂存区仅 Worker 用户可访问。
rename 后重新核对指纹，再以目录 fd 删除。每处理 100 项记录进度并让出 CPU，最后 fsync 删除和回执。
进程在 rename 前后崩溃，会检查源/暂存两处 inode；身份冲突即停止，不猜测、不删除替代目录。
部分删除不可撤销，回执报告已处理条目与失败原因。崩溃时进度计数可能少于实际已处理数量，不能用它计算精确空间回收。

断线时不开始新操作；已有持久 intent 可以恢复删除，对账回执保留到 API 确认。
根不可用或已接受操作暂时无法恢复时保留 journal 并重试，其他回执继续处理。
不要删除 journal 来“解除卡住”，不要手动移动暂存目录。先恢复目录挂载/权限或 API，核对操作 ID 与目录 inode，再由 Worker 对账。
清理暂存区不是回收站，不提供恢复承诺。

## 自动保留

默认 `enabled=false`、30 天，范围 1–3650 天。按 `Run.finished_at`，对所有终态一致处理；磁盘空间不足本身不会触发删除。
先 `POST admin/nodes/{id}/retention/preview`（`expected_revision, enabled, days`），再 `PUT .../retention` 加 `preview_id` 启用或更新已启用策略。
关闭使用相同版本控制，不需要启用预览。界面轮询保留草稿，版本冲突须重新加载再确认。
每小时每机最多产生 100 项自动操作，一机最多一项未结束自动操作；同策略版本已尝试条目不无限自动重试。
有效期规则也作用于符合条件的历史数据，预览只展示当前一批最多 100 项。
关闭或修改策略使未开始项失效；已获开始授权并持久接管的项可以完成自身恢复。关闭不代表恢复已删数据。
服务端在 Worker control 请求中按持久时间戳与机器行锁生成操作，不依赖某一 API 进程的内存定时器。

## 数据迁移、事务与回退

迁移 `h841c23` 在 `g730b12` 上增加三表：`execution_data`、`data_cleanups`、`data_cleanup_items` 及 Worker 策略字段。
盘点以 `(slot_id, root_id, entry_name)` 唯一，操作条目以 `(operation_id, data_id)` 唯一；各关联有 FK，常用查询字段有索引。
旧数据升级不产生清理操作，策略关闭。确认、盘点、开始授权与策略变更锁定机器行；存储配置保存也锁定同一行以串行化开始授权和配置切换。
这些短事务不执行文件 I/O，不持有全局调度锁等待扫描或删除。服务端只保存意图，文件操作由独立 Worker 线程完成。

上线先备份数据库及代码，上线兼容 API/UI，空闲升级 Worker，再请求只读盘点和预览。生产验收不提交清理，不启用自动保留。
回退先关闭策略并停止新增清理，处理 queued/running 和本地未决 journal，再降级 Worker/API。
Alembic 降级在策略启用或存在 queued/running 时拒绝。保留审计备份；降级和代码回退均不能恢复已删除文件。
