# SSH 纳管执行器部署

机器管理的 SSH 检查、注册与重新纳管，需要独立运行的 `onboarding.server`。API 只通过本地 Unix socket 调用它，执行器负责 SSH 和临时凭据；不对公网开放执行器端口。

出现 `SSH onboarding executor is not configured` 表示 API 缺少 ONBOARDING_SOCKET；`unavailable` 表示 socket 未挂载、执行器未运行或不可访问。不是目标机器密码错误。

## 安装与启动

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
