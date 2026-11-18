# SSH 纳管执行器部署

机器管理的 SSH 检查、注册与重新纳管，需要独立运行的 `onboarding.server`。API 只通过本地 Unix socket 调用它，执行器负责 SSH 和临时凭据；不对公网开放执行器端口。

## 默认部署无需用户配置

当前默认 Compose 包含 onboarding 服务，平台启动时自动构建并启动执行器、创建共享 socket 卷和独立状态卷，API 等待其健康后启动。用户只需在界面“机器管理 → 注册新机器”填写 SSH 信息、确认主机指纹和安装计划，无需安装 systemd 服务、填写 socket 或执行修复命令。

执行器通过内部 http://api:8000 回调平台，未对外开放端口；没有数据库/JWT 凭据，不挂载 Docker socket。仅执行器持有操作状态卷，API 只读挂载 socket 卷。停止平台不会删除这些卷。重启后活动操作显示中断，临时凭据失效，需要在界面重新检查，不会自动重放安装。

界面显示就绪状态；断连立即禁止新检查并自动每三秒重试，可点击“重新检测纳管服务”，已填内容不丢失。Docker 或网络本身故障仍属于平台运维，界面不会假装已自动修复。

默认无需设置 ONBOARDING_SOCKET/ONBOARDING_SOCKET_DIR。旧部署显式配置的宿主机 socket 继续优先使用；新内置执行器使用独立卷，不覆盖旧执行器的 socket、锁或记录。API 单独运行的自定义部署仍须由部署维护者提供执行器。

出现 `SSH onboarding executor is not configured` 表示 API 缺少 ONBOARDING_SOCKET；`unavailable` 表示 socket 未挂载、执行器未运行或不可访问。不是目标机器密码错误。

## 旧部署兼容：独立宿主机执行器（仅平台运维）

在平台宿主机为运行平台的普通用户准备项目虚拟环境，并安装 `onboarding/requirements.txt`。在 `~/.config/systemd/user/mini-eval-onboarding.service` 创建用户服务，以下 `/srv/clearml_mini` 和 `/home/platform` 要替换成实际项目、用户目录：

```ini
[Unit]
Description=Mini Eval SSH onboarding executor
After=network-online.target
[Service]
Type=simple
WorkingDirectory=/srv/clearml_mini
ExecStart=/srv/clearml_mini/.venv/bin/python -m onboarding.server --socket /home/platform/.local/share/mini-eval-onboarding/socket/control.sock --state /home/platform/.local/share/mini-eval-onboarding/operations --api-url http://127.0.0.1:18000
Restart=on-failure
RestartSec=3
UMask=0077
[Install]
WantedBy=default.target
```

API URL 必须是执行器可访问的平台地址。按本机实际发布端口修改。执行器自动创建 socket 和状态目录；先启动它，再重建 API，以免 Docker 创建 root 所有的目录。

```bash
systemctl --user daemon-reload
systemctl --user enable --now mini-eval-onboarding.service
systemctl --user status mini-eval-onboarding.service
```

确保用户 linger 已启用，注销后服务仍运行。状态目录不要共享给普通用户，socket 保持 0600；只有同 UID 或 root 的 API 进程可调用。不要通过 chmod 777 解决权限问题。

## 连接 API

在部署 `.env` 增加（不包含 SSH 密码）：

```dotenv
ONBOARDING_SOCKET=/run/mini-eval-onboarding/control.sock
ONBOARDING_SOCKET_DIR=/home/platform/.local/share/mini-eval-onboarding/socket
```

基础 Compose 已声明 socket 目录挂载，保留现有 LAN 和镜像覆盖配置，重建 API 生效。仅 restart 不会更新环境变量和挂载。不挂载执行器的 operations 目录，也无需挂载 SSH 私钥目录；凭据从纳管界面临时提交。

管理员登录平台 → 机器管理 → 注册新机器，纳管操作列表应可读取；再填写目标机器信息执行环境检查。读取列表成功只证明 API 到执行器连通，不代表目标机器 SSH 登录成功。后续版本发布检查同时保护 socket 环境和宿主机目录映射，若发生变化需要先核对部署配置。

## 本机修复记录（2026-11-16）

当前用户 dgh 已安装上述用户服务，项目目录 `/home/dgh/Code/clearml_mini`，状态位于 `~/.local/share/mini-eval-onboarding`，API 端口 18000。原部署无 socket 配置、无挂载且无持久执行器；补齐后验证认证纳管列表。未向任何目标节点发起新注册或重新纳管。

验证：纳管 API 与发布工具回归 50 passed；认证 GET /api/v1/admin/ssh-onboarding 返回成功；用户服务 active，linger=yes。后续 API 再次发布验证环境与挂载保持不变。未执行新目标机器的 SSH 登录测试。

## 默认执行器发布验证（2026-11-17）

当前控制端 API/前端已更新为 mini-eval-api:bundled-onboarding，并启动内置执行器 mini-eval-onboarding:dev；两者健康检查通过，本机和内网 18000 保留。现有显式宿主机执行器设置继续使用，认证列表读取成功。未改动节点 Agent/Worker，未启用远端已停止的旧平台。

隔离 mini-eval-onboarding-test 项目在没有任何 ONBOARDING_SOCKET 配置时完成全新 Compose 启动。真实 SSH 仅读取主机指纹，未确认指纹、发送认证或安装程序。验证执行器停止时 API 仍健康、纳管返回 503；恢复后操作记录保留且临时凭据失效；异常终止隔离执行器主进程后 Docker 自动重启并恢复连接。

回归：113 项后端测试、2 项浏览器测试、1 项真实容器集成测试通过。新 healthcheck 模块行与分支覆盖率均 100%，不表示全仓库覆盖率。首次故障注入在容器内发送信号未终止 PID 1；docker kill 又被当前 Docker 视为手动停止，最终改用严格限定隔离容器的宿主 PID 故障注入，验证自动恢复。失败原因保留于 .runtime/bundled-onboarding-test/first-failure.txt。

可重复集成测试位于 backend/tests/integration/test_bundled_onboarding.py：仅在明确设置 BUNDLED_ONBOARDING_TEST=1、BUNDLED_ONBOARDING_TEST_DIRECTORY（含私有 test.env）、BUNDLED_ONBOARDING_SSH_HOST 并准备 mini-eval-onboarding-test 隔离 Compose 项目（API 18194）时运行。测试会停止并异常终止该隔离执行器，严格验证 Compose 项目身份，不可指向生产。正常 CI 默认跳过此真机测试。隔离数据库及容器验收后删除，发布备份位于 .runtime/bundled-onboarding-release/，均不入库。

## HTTP 浏览器兼容性修复
通过 HTTP IP 地址访问时，浏览器可能不提供 crypto.randomUUID，旧注册页面会在发送 SSH 检查请求前报错。前端现统一生成请求 UUID，缺少原生方法时使用 getRandomValues；不需要用户调整浏览器开关。运行向导也使用相同方法，并保持失败重试复用幂等键。验证包含禁用 randomUUID 后完整注册界面流程及向导回归，不代表目标节点实际安装验证。其他机器需重新构建 API 镜像中的前端后才生效，仅重启旧镜像无效。

此修复验证：17 项独立浏览器场景通过，TypeScript/Vite 构建通过；模拟禁用原生 UUID，不宣称在无法访问的远端浏览器完成验收。本次提交未自动更新远端部署。

 
## cgroup 兼容与 runner 准备（0.17.1）

root 系统宿主机支持 cgroup v1、v2 和混合层级，以实际 CPU、内存、进程数限制验证为准；挂载点显示 tmpfs 不等于不支持。非 root 旧用户服务仍要求 v2 委派，不自动迁移身份或修改引导参数。单纯重启旧镜像不会获得修复，控制面安装包与 SSH 执行器需要同步更新，已部署节点按升级/重新纳管流程更新。

Docker-only 纳管不运行宿主机 systemd 限制探针；只要求 Docker daemon 可用。runner 镜像是否存在仅作为提示，不阻止安装，不自动拉取、构建或试运行容器。页面按实际镜像提供命令：默认 runner 在项目根目录或完整节点安装包解压目录执行 `docker build -f runner/Dockerfile -t mini-eval-runner:dev .`；自定义镜像执行 `docker pull <实际镜像名>`。离线或仓库失败由管理员准备镜像后再运行业务。模板镜像继续由机器“容器环境”批准、Worker 探测及调度检查；纳管成功不表示容器资源限制、NPU/CANN 或模型业务已验收。

新 root SSH 纳管可填写工作目录根路径，留空使用 `/var/lib/mini-eval/workspaces`。检查计划逐卡预览 `根路径/机器名/gpu0` 或 `根路径/机器名/npu3` 作为独立工作目录；资产、缓存分别使用 `根路径/机器名/assets`、`根路径/机器名/cache`，由同机各卡共享。安装器复核后创建缺失目录。已有目录及文件保留，重新纳管保留原每卡目录，不随新输入迁移。

已有 root 自定义目录安装可从独立 updater 的可信布局原位恢复，保留 Worker 单元、业务账号和数据路径。布局、配置或服务变化需重新检查；缺失布局、权限不可信、遗漏设备和恢复记录未清理仍拒绝。安装包必须包含 scripts/node_layout.py，API 与执行器同步发布。详见 [重新纳管](node-reenrollment.md)。真实验证范围及失败记录见 [cgroup 与重新纳管验证](validation/cgroup-runtime/README.md)。

若缺 runner 导致存储校验失败，准备镜像后在“存储”页点击“保存存储配置 / 重试校验”；业务镜像在“容器环境”批准后等待 Worker 上报就绪。

## 控制面 401 与清理结果

`control_api_rejected_401` 是执行器调用平台 API 的认证失败，不是 SSH 密码错误。旧实现从环境检查开始一直保存同一个登录令牌，浏览器续期不能更新执行器。确认主机指纹和确认执行现在都会在管理员权限校验后向执行器传递当前请求的令牌；令牌只在内存使用，不写操作记录。若仍为 401，重新登录并重新检查，排查签名密钥、账号状态及令牌有效期；本修复不把浏览器凭据变成无限期后台授权，12 小时登录上限和认证撤销仍有效。

“远程临时目录清理未确认”单独表示安装结束时移除 `/tmp/mini-eval-node.*` 未得到确认，不能据此认定安装失败原因或业务目录损坏。确认目标没有活动纳管后只处理对应操作的临时目录，避免通配删除并发操作。

SSH 残留清理复用执行器的主机指纹确认、临时凭据和目标互斥机制，以 `kind=cleanup` 区分操作。检查与确认均校验平台机器 revision；实际清理前取得平台 RUNNING 许可，阻止删除/重新纳管。远端结果先落到执行器脱敏记录，再上报 API；若回报认证失败，管理员刷新列表会从专用 socket 核对结果，不能由浏览器伪造成功。执行器重启导致未确认操作时保留 FAILED 状态，重新检查后才能重试。清理结果在平台确认前不自动过期；确认后沿用操作记录保留策略。结果不明时应核对备份清单。

此功能更新涉及 API、前端、数据库迁移和 onboarding 执行器；只刷新浏览器或只重启 API 不会更新独立执行器代码。无需全节点升级或重新纳管。

## 回连超时与可选探测依赖

回连验收默认等待 300 秒，每 2 秒检查一次。执行器环境变量 `ONBOARDING_RECONNECT_TIMEOUT_SECONDS` 可设为 30–1800 的整数；Compose 默认传入 300，修改后需重建执行器容器以加载配置。安装脚本的远程命令超时仍单独计算，回连窗口从安装结束后开始。

Worker 启动时在后台准备固定的 aiohttp、aiodns、async-timeout 依赖，注册和心跳不等待 pip 或安装锁。日志标记 preparing / ready；失败报告权限、磁盘、DNS、代理、证书、包/Python 不兼容、pip 退出码、安装超时或异常类型，不记录可能含凭据的 pip 原始输出。失败后本进程不自动循环安装，修复网络/权限后在合适维护窗口重启对应 Worker 可重试。界面按设备显示探测能力是否就绪；没有探测依赖不阻止回连/版本验收，但需要该能力的服务模板仍被业务准入拦截。后台准备成功后通过心跳更新能力，无需再次纳管。

安装完成但回连超时，或执行器在回连阶段重启后，可在原操作点击“继续回连验收”。只查询平台状态并尝试完成原安装，不重新连接 SSH、不覆盖配置、不轮换节点 token；使用当前登录凭据。节点修订或发布摘要已经改变则拒绝旧操作。仍处于安装中或旧版本没有保存可继续信息的操作不显示此按钮。

`worker iteration failed` 与 `execution data maintenance deferred` 后若堆栈在 HTTP `getresponse()` 报 `TimeoutError`，表示等待 API 响应超过 Worker 的 5 秒请求超时。它与 300 秒纳管验收窗口不同；延长验收不会修复代理、API 或数据库阻塞。先在节点用不带凭据的 `/healthz` 直连测试，再检查服务进程代理及相同时间的 API 请求/数据库等待日志；健康接口正常不代表心跳写入正常。Agent 是权威遥测来源时还需检查 Agent，资源遥测有效期现为 45 秒；该时间与回连窗口和 Worker HTTP 请求超时分别判断。

发布需同步 API、执行器、UI 和节点 Worker；只改执行器无法消除旧 Worker 的启动等待。未在用户节点实际验证前不得宣称现场超时已恢复。

### 现场 HTTP 超时的最小检查

以下命令由能访问现场的运维人员执行，平台地址替换成 Worker 实际使用的入口；不需要节点 token。

```bash
curl --noproxy '*' --connect-timeout 5 --max-time 15 -sS -o /dev/null \
  -w 'HTTP=%{http_code} connect=%{time_connect}s first_byte=%{time_starttransfer}s total=%{time_total}s\n' \
  http://平台IP:端口/healthz
curl --noproxy '*' --connect-timeout 5 --max-time 15 -sS -o /dev/null \
  -w 'HTTP=%{http_code} connect=%{time_connect}s first_byte=%{time_starttransfer}s total=%{time_total}s\n' \
  http://平台IP:端口/readyz
```

`/healthz` 只确认 API 进程存活；`/readyz` 会执行数据库查询，仍不保证心跳事务无锁等待。若前者快、后者慢，优先检查数据库连接/负载；若两者都快，继续对照同一时刻的心跳请求与 Worker 服务进程代理配置。不要直接公开 `systemctl show Environment` 或 `/proc/PID/environ`，其中可能含凭据。

数据库管理员可使用现有授权连接执行只读检查，不输出 SQL 文本或连接密码：

```sql
SELECT pid, state, wait_event_type, wait_event,
       now() - query_start AS query_age,
       now() - xact_start AS transaction_age,
       pg_blocking_pids(pid) AS blocking_pids
FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid()
ORDER BY xact_start NULLS LAST;
```

Worker 心跳经过调度事务锁；数据维护 control 会锁定节点记录。二者超时可以是数据库等待的表现，也可能发生在代理/网络层，不能仅凭客户端栈确定。先定位等待来源，不以强改状态、删除恢复记录或批量终止数据库事务代替修复。
