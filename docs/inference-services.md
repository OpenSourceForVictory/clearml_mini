# 推理服务实例

管理设置 → 机器管理 → 服务端机器 → 推理服务实例。机器必须启用 server 角色（可兼任 client），目标 GPU 已加入启用的普通资源池；不创建推理服务池。先在运行模板管理注册服务端模板，再部署实例。

部署指定模板版本、目标 GPU、该机器实际 IPv4、宿主机端口、独占/共享及显存申请。模板的端口是容器内端口，实例端口是宿主机端口；Docker仅绑定提交的具体IP，host模式通过 MINI_EVAL_PORT/MINI_EVAL_BIND_HOST 或 `${port}`/`${bind_host}` 传给业务程序。地址必须确实属于目标机器，启动前会检查可绑定且端口未被占用。

GPU、CPU、内存及并发使用现有 Job/Run 资源账本。CPU/内存按资源池每任务额度计算；没有资源时先排队。服务未清理时不能复用资源或端口。停止后再“启动新执行”会创建新的执行身份，保留原快照和日志；禁用/归档模板后不能新启动。

服务启动健康期限由模板 startup_timeout 决定，HTTP 检查只接受目标地址的2xx且不跟随重定向；TCP只证明端口连通。只有新鲜心跳和健康报告同时成立才显示健康。先前健康的服务后来探测失败会显示异常，供管理员查看日志和停止；机器离线显示健康未知，未确认清理继续保留资源。

服务不是有限回合评测，不需要最终 success_rate，也不会因普通评测的全局执行时长到期退出。Worker租约仍有效，失联后会停止本地进程；运行意外退出视为服务失败，平台不会自动无条件重启。当前实现保留一年执行器上限；长期维护应有计划滚动重启。

API：GET/POST `/api/v1/admin/services`；POST `/{id}/control` 带 action=start/stop、expected_revision；GET `/{id}/logs` 可用 job_id 查看历史执行。日志有界，停止是期望状态，实际 STOPPED 要等进程终止与清理确认。

复制实例地址后，评测仍通过现有推理地址列表选择端点并配置独占/共享。部署实例不会自动改写已提交评测的地址，也不会把健康异常实例强制替换成其他服务。

验收样本 `runner.inference_demo` 是协议夹具，不是模型服务。`scripts/smoke_services.py` 在隔离18090 API上用两个模拟GPU槽位测试真实 Docker/host 服务、健康、客户端请求、日志、停止和重启历史。

服务端模板可定义 `parameter_schema`，与客户端使用相同的类型、默认值、覆盖权限及 argv/env/input 绑定。部署提交 `parameters`，可覆盖字段使用 `parameter_overrides`；缺失必填项、错误类型或越权覆盖会拒绝整个部署，不创建实例或任务。最终值和模板版本保存为不可变执行快照，停止后启动新执行仍沿用原参数。类型化服务要求 Worker 8。部署表单目前用 JSON 输入业务值，并展示所选模板定义。

2026-11-13 验证：服务 API/类型参数/健康探测测试、调度与执行关联回归、前端模板/管理/服务操作测试及构建；隔离 PostgreSQL 从 l285a67 升级到 m396b78，验证旧模板兼容和服务参数列。`scripts/smoke_services.py` 再次通过 Docker、host 两模式（模拟 GPU 槽位，真实进程与 HTTP 客户端），涵盖类型化模板、健康、请求、日志、停止及重启。当前尚未部署生产或升级五个 Worker；启动失败/清理恢复专用故障注入、通用挂载仍未完成。

## 故障恢复验收

`backend/tests/test_service_recovery.py` 使用真实 Docker 容器与 systemd 用户单元，在 API 边界注入故障，不占用 GPU。运行前需本地 `mini-eval-runner:dev` 镜像、Docker 和 systemd 用户会话：

```bash
HOST_EXECUTION_TEST=1 .venv/bin/python -m pytest backend/tests/test_service_recovery.py backend/tests/test_host_backend.py backend/tests/test_worker_recovery.py -q
.venv/bin/python -m pytest backend/tests/test_services.py backend/tests/test_execution.py backend/tests/test_scheduling.py -q
```

覆盖非零退出、零退出但伪装评测 final、HTTP 启动健康超时（含报告上传故障）、健康报告短暂丢失、租约到期停止、重建 Worker 后确认清理，以及取消时 systemd 自动卸载临时单元。短暂健康报告上传失败会重试，不主动结束仍持有租约的服务；本地启动健康期限和租约看门狗仍生效。API 测试验证超时执行在清理确认前继续占用 GPU 和端口，清理后才允许排队任务及同端口新实例进入。

故障测试中的 API 为注入夹具；真实 API、数据库及客户端联通另由 `scripts/smoke_services.py` 验证。TCP 探测只表示端口可连接，Docker 端口代理也可能接受连接，因此需要业务就绪语义时应使用 HTTP 健康路径。

### 旧 Worker 的服务实例日志

服务实例“日志”入口按所选执行读取日志。存在新版日志状态时读取 v2 有界分片窗口；否则读取旧版日志，停止后的历史执行也可查看。不会把两种格式重复拼接，也不按实例名称查找，因此同名实例不会串日志。

2026-11-20 真机验证发现：Worker 已上传 v2 分片时，旧服务日志接口仍只读取旧表，表现为任务日志有内容而服务实例日志为空。修正 API 后既有数据即可显示，无需重新运行、数据库迁移或升级节点；此入口仍是保留窗口，不承诺完整磁盘日志。

探测会话初始化遇到控制面 503 或网络超时，新 Worker 会在启动时限内重试并持续续租，保留已启动进程；取消、租约失效或启动超时仍会停止执行。此修复同时用于 host/Docker，需要更新 Worker。未更新的节点可能在会话申请失败后停止服务，恢复时表现为 unit missing；应先查 Worker 日志中的原始 API 错误，不删除恢复记录强制重复启动。

服务实例“日志”区域可打开独立服务日志；切换执行历史时，独立抽屉和摘要都绑定所选执行。无新日志能力的旧执行保留摘要，不能补造历史独立日志。
