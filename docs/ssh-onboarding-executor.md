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
