# Design

## Context
用户确认产物位于共享存储，第一版改为 API 只读挂载后直接读取；Worker 文件转发暂缓。既有 2a4aee1 的节点身份切换/转发 helper 保持未启用；纯文件读取器 artifact_reader 由 API 隔离子进程复用。仓库默认 deploy/compose.yml 尚未挂载产物共享目录；必须提供独立的挂载/映射配置，不能据节点可见路径假定 API 已可见。

见 proposal.md。现有 RunArtifact 用 (run_id,name) 唯一键、deferred LargeBinary 正文；worker/artifacts.py 从容器固定输出目录 docker cp tar 流读取，host_backend.artifacts 从 runs/<execution>/output 收集。旧上传接口锁调度事务且要求活跃租约；单文件 8 MiB、8 文件/32 MiB，不适用于视频。SDK Context 当前仅有任务级 output_dir；host_episode 已将子进程输出分到 episode-N。已有日志 mailbox 提供异步节点读取，但 JSONL 协议、鉴权和配额均不能直接用于媒体字节。

## Goals / Non-Goals
Goals: 用户在批次、任务、回合中查看共享存储上的平台输出目录或自定义目录，明确两者时效和归属；保持历史、业务结果和 Docker 隔离；大文件内存有界、心跳不阻塞。
Non-Goals: 自定义目录自动收集、文件修改/删除、自动转码、全节点文件管理、集中产物存储、主动上传、Worker 文件请求邮箱/转发通道、迁移节点工作目录或将目录名作为业务归属证据。服务实例文件浏览本轮不扩展客户端任务模型。

## Decisions
1. **文件留在共享存储。** 保留 runs/<execution>/output，不新增集中产物副本或正文上传。数据库保存 run/node/slot/execution、输出根和回合归属及必要索引元数据，不存文件正文或文件读取邮箱。API 从只读挂载直接索引/读取。平台输出目录自动关联执行；自定义目录只读浏览，不冒充本次任务产物。跨尝试同名隔离，索引不是备份。
2. **SDK 兼容。** output_dir 保持任务级含义；新增只在 run_episode 上下文有效的 episode_output_dir，每回合独立 context，首次访问属性时创建目录；避免旧 SDK 业务仅因升级就产生大量空目录、触发旧 Worker 扫描上限。SDK 提供相对任务根的产物路径转换方法，不改变旧 EpisodeResult.artifacts 的任务根相对语义。setup 使用任务 context。每回合子进程已在回合目录时，不再次套嵌；父 Runner 负责转换相对路径。SDK 管理目录通过专用 manifest 明确登记回合，不从任意 episode-N 名字推断。协议明确声明优先；文件允许多个回合显式引用。不存在的声明显示未生成，不能成为任意路径读取入口。
3. **目录登记与业务完成分离。** Worker 上报执行已解析的共享输出根和明确回合归属，API 将其绑定到实际 run/slot/execution；不得接受浏览器提交任意绝对路径替代。API 排除协议 journal/manifest 和日志，按需扫描普通输出文件。异常退出仍保留已登记输出；索引或预览失败不改业务结果、不重启业务。已删除批次不得被迟到登记复活。
4. **宿主机与容器保留。** 宿主机继续使用执行输出根；现有 cs-client/inference-service Docker 执行已把平台输出目录绑定到节点持久工作目录，后续索引直接复用该挂载，容器退出/删除不删除输出。旧容器未配置该挂载时明确能力缺失，不承诺删除后可恢复。不复制到平台、不分块上传、不创建上传队列。节点清理仍可按既有保留策略清除文件，清理后界面显示已清理。旧数据库正文及旧 Worker 上传入口保留兼容，新能力协商成功的 Worker 不再主动上传。
5. **共享根映射与快照。** 管理员配置稳定 storage_id、适用节点及节点路径前缀到 API 挂载路径的映射；按路径组件匹配，拒绝歧义和越界，不做裸字符串替换。执行保存 storage_id、相对输出根和已解析目录快照，历史不得从最新模板/节点配置补造。相同 storage_id 的 API 挂载位置可调整，但更换底层共享存储必须更换身份，不能把历史任务指向其他内容。模板自定义目录仅允许批准共享根内的已解析目录；容器通过既有持久绑定映射到同一共享存储，临时层不支持。
6. **API 直接只读。** API 服务账号拥有共享目录最低必要读取/穿越权限，挂载 readonly；不在 API 主机冒充远端业务 UID。每次请求先校验任务可见性、执行目录快照和当前共享根授权，再以逐层 fd/no-follow 校验读取，拒绝穿越、链接及特殊文件。每页最多 100 项、每块最多 1 MiB，检查文件版本，取消或超时释放读取资源；慢 NFS I/O 使用有界独立执行资源，不阻塞 API 事件循环、心跳或持有数据库连接。挂载不可用、映射缺失、文件缺失/变化、权限不足分别提示；不得把未挂载的空目录当作没有产物。节点离线不阻止读取仍可用的共享文件。API 多进程/副本必须具备相同只读映射；Worker 文件通道不实施。
7. **媒体鉴权。** 首先通过 Bearer 验证授权，再签发短期、绑定用户和具体 run/文件的 HttpOnly SameSite cookie（路径隔离）；video src 不放用户 token，访问每个 Range 都重新校验用户活跃和任务可见性。支持 GET/HEAD、单范围 bytes=start-end/start-/suffix、206/416、Content-Range/Length、Accept-Ranges、ETag 和 If-Range；不支持多范围返回明确错误。历史正文与共享文件复用媒体层。URL 无凭据，响应 no-store/nosniff；只允许视频/图片白名单内联，HTML/SVG等作为下载，文本限量按纯文本渲染。
8. **界面。** 批次任务行“查看产物”打开右侧 modal，保留筛选/滚动；回合入口预选回合。面板可切执行尝试，两个 tabs：运行产物/自定义共享目录。文件列表显示名称、回合、大小、状态、查看/下载，分页按需加载；任务级/全部/回合筛选。视频面板内原生播放器，进度拖动/全屏/下载，编码不支持提示。目录页显示存储标识/执行来源节点/根路径/实时内容提示/面包屑，首版仅配置根内浏览。切任务、尝试、回合或关闭时取消旧请求和播放，不混淆历史数据；窄屏全宽、键盘关闭和焦点恢复。 JSON 限量加载、格式化及折叠，解析失败显示原文与错误；TXT/LOG 纯文本；CSV 有界表格；PNG/JPEG/WebP/GIF 图片；MP4/WebM 视频。文本最多读取 256 KiB，超限明确截断而不伪装完整 JSON；表格最多 200 行、50 列，JSON 树限制展开节点。HTML/SVG 仅纯文本或下载，不执行。
9. **借鉴边界。** ClearML Task.upload_artifact 显式登记文件、Logger.report_media 用 iteration 关联媒体，MLflow 将元数据和 artifact store 分开。我们增加评测回合目录便利能力；不照搬同名覆盖、debug samples 历史淘汰或任意自定义路径上传。参考 https://clear.ml/docs/latest/docs/fundamentals/artifacts/ 、https://clear.ml/docs/latest/docs/references/sdk/logger/ 、https://mlflow.org/docs/latest/self-hosting/architecture/artifact-store/ 。

## Risks / Trade-offs
- [大视频耗尽带宽/内存] → 单块有界、有限并发、取消读取与 backpressure；数据库不存新视频正文，也不缓存完整文件。
- [容器删除/节点离线] → 容器输出持久绑定；共享存储可读时不依赖 Worker 在线。文件被清理或共享挂载不可用时明确提示，不承诺备份。
- [共享固定目录有其他任务文件] → 标为实时目录，只有批准目录访问权限，不将内容宣称为本次执行产物。用户负责目录隔离。
- [共享根权限变化] → 立即按当前共享根授权与 API 服务账号权限拒绝读取；不得因 API 挂载较广就允许访问其他任务文件。
- [旧 SDK path 语义变化] → 保留旧字段及旧相对路径含义，新增字段，不迁移目录。
- [下载授权泄漏或会话注销] → 不在 URL 放 token，短时 cookie 严格 path，逐次权限检查及过期；测试会话失效和跨任务尝试。

## Migration Plan
按最终元数据模型确认目录/索引是否需要迁移，保留 RunArtifact 旧正文不变，不新增请求邮箱表或集中产物卷。共享存储须先在 API 宿主可见，再以只读绑定挂入 API；每次读取校验映射与挂载身份，缺目录不得自动创建空目录冒充挂载。然后升级 API，按目录登记和停止旧上传的能力需求升级 Worker/Runner。旧 Worker 不因 API 更新自动停止上传；兼容状态须明确。备份 DB 不等于备份共享文件；回滚保留元数据和共享文件，不从最新配置重构历史。

## Acceptance and Tests
详细矩阵见 test-plan.md，单测覆盖正常/失败/边界；浏览器 mock 只证明交互，真实宿主机/容器及浏览器 Range 必须单独留证。新代码按项目统一覆盖率统计，不降低阈值，不把测试数量作为覆盖率。真机验收批次在指定平台保留可查看记录，记录 URL/批次 ID/执行 ID/文件 hash；隔离平台必须明确地址和保留范围，不宣称当前生产页面可见。

## 当前 API 直读实施切片（2026-11-19）
新增 SharedArtifactRoot / a730d12，只保存不可重绑的执行输出目录映射；不保存逐文件索引。配置及只读 overlay 见 docs/shared-storage-artifacts.md。新 Worker 0.21.9 按任务标记停止正文上传；旧 Worker 保留兼容。当前侧栏以共享/历史上传选择器区分来源，自定义目录页与 SDK manifest 仍未完成。读取及媒体流各限每 API 进程8个、每用户2个，10秒 I/O 超时；媒体授权最长5分钟，流跨过有效期需重新授权。已先释放 DB 连接再启动文件 I/O。

## 已批准调整：独立节点产物根（2026-11-19）
节点存储配置增加 artifact_root。未配置保持旧 runs/<execution>/output；配置后新执行生成 <artifact_root>/<batch_id或standalone>/<job_id>/<execution_id>/output，逐回合输出为同一执行根下 episodes/<n>/output。业务 cwd 不变。宿主机以批准业务账号创建目录，容器绑定整个执行产物根并传容器内输出路径；SDK 单进程保持公共 output_dir、episode_output_dir 指向独立回合，嵌套不重复分层。平台以实际执行目录登记浏览根，历史绝不根据新配置重算。工作区清理不得删独立产物。旧 Worker 不具备能力时阻止新配置生效。节点路径仍须与 API 共享根映射对应。未实现自定义目录浏览/转发不纳入本切片。
