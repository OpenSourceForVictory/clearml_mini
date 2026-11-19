# Design

## Context
见 proposal.md。现有 RunArtifact 用 (run_id,name) 唯一键、deferred LargeBinary 正文；worker/artifacts.py 从容器固定输出目录 docker cp tar 流读取，host_backend.artifacts 从 runs/<execution>/output 收集。旧上传接口锁调度事务且要求活跃租约；单文件 8 MiB、8 文件/32 MiB，不适用于视频。SDK Context 当前仅有任务级 output_dir；host_episode 已将子进程输出分到 episode-N。已有日志 mailbox 提供异步节点读取，但 JSONL 协议、鉴权和配额均不能直接用于媒体字节。

## Goals / Non-Goals
Goals: 用户在批次、任务、回合中查看节点平台输出目录或自定义目录，明确两者时效和归属；保持历史、业务结果和 Docker 隔离；大文件内存有界、心跳不阻塞。
Non-Goals: 自定义目录自动收集、文件修改/删除、自动转码、全节点文件管理、集中产物存储、主动上传、迁移节点工作目录或将目录名作为业务归属证据。服务实例文件浏览本轮不扩展客户端任务模型。

## Decisions
1. **文件留在节点。** 保留 runs/<execution>/output，不新增集中产物卷或正文上传。数据库只保存 run/node/slot/execution、目录注册、文件元数据、回合归属及读取请求状态。平台输出目录自动建立索引；自定义目录实时浏览，不冒充任务产物。跨尝试同名文件隔离。索引不是文件备份；节点离线、文件清理和权限变化需要实时确认。
2. **SDK 兼容。** output_dir 保持任务级含义；新增只在 run_episode 上下文有效的 episode_output_dir，每回合独立 context，首次访问属性时创建目录；避免旧 SDK 业务仅因升级就产生大量空目录、触发旧 Worker 扫描上限。SDK 提供相对任务根的产物路径转换方法，不改变旧 EpisodeResult.artifacts 的任务根相对语义。setup 使用任务 context。每回合子进程已在回合目录时，不再次套嵌；父 Runner 负责转换相对路径。SDK 管理目录通过专用 manifest 明确登记回合，不从任意 episode-N 名字推断。协议明确声明优先；文件允许多个回合显式引用。不存在的声明显示未生成，不能成为任意路径读取入口。
3. **索引与业务完成分离。** Worker 登记平台输出根与明确的回合归属，回合结束和执行结束更新索引，排除协议 journal/manifest 和日志；异常退出仍登记已有输出。失败不改变业务结果、不重新执行业务。运行中变化文件标记为实时内容，读取时使用文件身份校验。索引持久化以支持 Worker 重启后历史读取；已删除批次的迟到登记不能复活记录。
4. **宿主机与容器保留。** 宿主机继续使用执行输出根；现有 cs-client/inference-service Docker 执行已把平台输出目录绑定到节点持久工作目录，后续索引直接复用该挂载，容器退出/删除不删除输出。旧容器未配置该挂载时明确能力缺失，不承诺删除后可恢复。不复制到平台、不分块上传、不创建上传队列。节点清理仍可按既有保留策略清除文件，清理后界面显示已清理。旧数据库正文及旧 Worker 上传入口保留兼容，新能力协商成功的 Worker 不再主动上传。
5. **节点目录快照。** 客户端模板增加可选 result_browser.directory，支持项目已有平台/业务参数解析语法；提交保存模板配置，启动解析后登记最终 root、运行账号 uid/gid/groups、节点/slot/执行及批准根身份。历史缺配置不从最新模板补齐。宿主路径只允许批准代码/资产/工作根范围；容器目录只能通过持久挂载映射到批准宿主根，不读容器临时层。节点重新批准或撤销权限后拒绝旧授权，展示具体原因。未配置不发节点请求。
6. **只读文件服务。** 独立 Worker 后台通道，进程使用记录的业务账号并复核当前批准配置；API 只传 registry id + 安全相对路径。逐层 fd/no-follow 校验，拒绝穿越、链接、FIFO/设备文件及账号无权限，读取子进程限时。分页上限 100 项；每块最多 1 MiB；文件 inode/size/mtime 校验避免视频 Range 串版本。节点离线或文件变化返回可识别状态，不能回退 root 权限。每用户/节点/全局限制并发，队列请求带有效期；过期结果丢弃。短事务 mailbox 仅存元数据，字节写受限临时文件或响应流，避免 DB 变成视频中转存储。
7. **媒体鉴权。** 首先通过 Bearer 验证授权，再签发短期、绑定用户和具体 run/文件的 HttpOnly SameSite cookie（路径隔离）；video src 不放用户 token，访问每个 Range 都重新校验用户活跃和任务可见性。支持 GET/HEAD、单范围 bytes=start-end/start-/suffix、206/416、Content-Range/Length、Accept-Ranges、ETag 和 If-Range；不支持多范围返回明确错误。历史正文与节点文件复用媒体层。URL 无凭据，响应 no-store/nosniff；只允许视频/图片白名单内联，HTML/SVG等作为下载，文本限量按纯文本渲染。
8. **界面。** 批次任务行“查看产物”打开右侧 modal，保留筛选/滚动；回合入口预选回合。面板可切执行尝试，两个 tabs：运行产物/节点目录。文件列表显示名称、回合、大小、状态、查看/下载，分页按需加载；任务级/全部/回合筛选。视频面板内原生播放器，进度拖动/全屏/下载，编码不支持提示。目录页显示节点/根路径/实时内容提示/面包屑，首版仅配置根内浏览。切任务、尝试、回合或关闭时取消旧请求和播放，不混淆历史数据；窄屏全宽、键盘关闭和焦点恢复。 JSON 限量加载、格式化及折叠，解析失败显示原文与错误；TXT/LOG 纯文本；CSV 有界表格；PNG/JPEG/WebP/GIF 图片；MP4/WebM 视频。文本最多读取 256 KiB，超限明确截断而不伪装完整 JSON；表格最多 200 行、50 列，JSON 树限制展开节点。HTML/SVG 仅纯文本或下载，不执行。
9. **借鉴边界。** ClearML Task.upload_artifact 显式登记文件、Logger.report_media 用 iteration 关联媒体，MLflow 将元数据和 artifact store 分开。我们增加评测回合目录便利能力；不照搬同名覆盖、debug samples 历史淘汰或任意自定义路径上传。参考 https://clear.ml/docs/latest/docs/fundamentals/artifacts/ 、https://clear.ml/docs/latest/docs/references/sdk/logger/ 、https://mlflow.org/docs/latest/self-hosting/architecture/artifact-store/ 。

## Risks / Trade-offs
- [大视频耗尽带宽/内存] → 单块有界、有限并发、取消读取与 backpressure；数据库不存新视频正文，也不缓存完整文件。
- [容器删除/节点离线] → 新容器输出持久挂载；节点离线、文件被清理时明确不可用，不承诺归档或离线浏览。
- [共享固定目录有其他任务文件] → 标为实时目录，只有批准目录访问权限，不将内容宣称为本次执行产物。用户负责目录隔离。
- [账号被删或权限收紧] → 拒绝节点浏览，不隐式提权；历史数据库文件仍按任务权限读取。
- [旧 SDK path 语义变化] → 保留旧字段及旧相对路径含义，新增字段，不迁移目录。
- [下载授权泄漏或会话注销] → 不在 URL 放 token，短时 cookie 严格 path，逐次权限检查及过期；测试会话失效和跨任务尝试。

## Migration Plan
新增节点目录/文件索引与请求元数据迁移，保留 RunArtifact 旧正文不变；不新增集中产物卷。先在一次性 PostgreSQL 验证迁移与批次删除，再升级 API，最后升级 Worker/Runner。旧 Worker 行为明确标识，升级后才停主动上传。历史不补造回合和目录归属。回滚保留新元数据和节点文件；旧 API 不支持新节点索引。备份数据库不等于备份节点产物，节点保留策略及清理影响须在升级指南中说明。

## Acceptance and Tests
详细矩阵见 test-plan.md，单测覆盖正常/失败/边界；浏览器 mock 只证明交互，真实宿主机/容器及浏览器 Range 必须单独留证。新代码按项目统一覆盖率统计，不降低阈值，不把测试数量作为覆盖率。真机验收批次在指定平台保留可查看记录，记录 URL/批次 ID/执行 ID/文件 hash；隔离平台必须明确地址和保留范围，不宣称当前生产页面可见。
