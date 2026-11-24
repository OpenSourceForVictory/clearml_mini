# 已有平台切换双 API：启动与更新

适用于已有数据库和管理员的标准 Compose 部署。只运行 `deploy/compose.yml` 或 `scripts/control.py` 仍是单 API；双 API 必须始终加 `deploy/split-api-compose.yml`。两个 API 加一个网关，不是启动两个独立平台。

## 1. 保留原部署身份

在仓库根目录执行。保留原 `.env`、数据库卷和 Compose 项目名。下面假设项目名为 `mini-eval`；自定义项目名必须替换，不能另建项目后误以为数据丢失。可用现有 API 容器查询原项目名与配置文件（容器名称按实际替换）：

```bash
docker inspect mini-eval-api-1 --format '{{ index .Config.Labels "com.docker.compose.project" }}'
docker inspect mini-eval-api-1 --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}'
git pull --ff-only
docker compose version
```

要求 Compose >= 2.24（需要 `!reset`）。不要覆盖已有 `.env`，不要执行 `down -v`。原来有镜像或共享存储覆盖文件的，应保留并核对其同时作用于两个 API；不要把本机 `.runtime/` 下的发布覆盖文件路径原样复制到其他机器。

## 2. 固定同一启动命令

标准配置：

```bash
dc() {
  docker compose --env-file .env -p mini-eval \
    -f deploy/compose.yml \
    -f deploy/split-api-compose.yml "$@"
}
dc config --quiet
dc config --services
```

输出应包含 `api`、`control-api`、`api-gateway`。原平台地址继续由 `.env` 的 `API_BIND_HOST` 和 `API_BIND_PORT` 决定，网关接管该入口，Worker 无需改地址。若 API_BIND_HOST 为 127.0.0.1，只允许本机访问。

如果原部署还有第二个 Worker 私网入口，保留 `.env` 的 `WORKER_BIND_HOST`，将函数替换为：

```bash
dc() {
  docker compose --env-file .env -p mini-eval \
    -f deploy/compose.yml \
    -f deploy/split-api-compose.yml \
    -f deploy/split-api-lan-compose.yml "$@"
}
```

不要同时带原 `deploy/lan-compose.yml`，否则会给普通 API 重新绑定端口，与网关冲突。原有自定义覆盖文件还需按实际顺序补入函数。

## 3. 构建、备份和切换

先确认没有活动任务或节点安装/升级操作。双进程默认合计最多 80 个 API 数据库连接；确认数据库还有管理、迁移等连接余量，详见 [运维配置](operations.md)。构建先进行，构建失败时旧平台仍可用：

```bash
dc build api control-api onboarding
```

已有平台数据库备份（标准 compose 数据库名和用户均为 eval）：

```bash
mkdir -p .runtime/db-backups
chmod 700 .runtime/db-backups
backup_file=".runtime/db-backups/before-split-api-$(date +%Y%m%d-%H%M%S).dump"
(umask 077; dc exec -T db pg_dump -U eval -d eval -Fc > "$backup_file")
test -s "$backup_file"
dc exec -T db pg_restore --list < "$backup_file" > /dev/null
```

上述任一步失败，先解决错误，不继续切换。确认备份有效后逐条执行，任一步失败不要执行下一条：

```bash
dc stop api control-api api-gateway
dc run --rm --no-deps migrate
dc up -d --no-deps --wait api control-api api-gateway onboarding
```

这里显式停止两组 API 再迁移，避免不兼容版本混写；已有管理员不需要重跑 bootstrap。`--no-deps` 适用于数据库已运行的已有平台，不是空机器首次安装命令。若有镜像覆盖，保证 migrate 与两个 API 使用同一版本。

## 4. 验证

```bash
dc ps
dc logs --since 5m api control-api api-gateway
dc exec -T api-gateway wget -q -O - http://127.0.0.1:8000/healthz
dc exec -T control-api printenv SCHEDULER_ENABLED
dc exec -T api alembic -c backend/alembic.ini current
```

应看到 `api`、`control-api`、`api-gateway` 都 healthy，控制 API 的 SCHEDULER_ENABLED 输出 0，只有普通 API 运行调度器。用原平台地址登录并观察节点回连；不要输出完整容器环境或 `.env`。只有一套 API 时先检查 `dc config --services`；存在但退出时查该服务日志，不要重复增加容器副本。

## 5. 后续更新

继续使用同一个 dc 函数及覆盖文件组合，重复构建、备份、停止 API、迁移、启动、验证步骤。不要改回基础 Compose 命令或 scripts/control.py。更新 API 不会自动升级远端 Worker；本次预约窗口和服务补齐公平性修复不要求 Worker 升级，未来断联恢复协议需另行双端验收。

首次空机器安装仍按原初始化指南准备 `.env`，构建后使用双 API 文件组合执行完整 `up -d --wait`，让 db/migrate/bootstrap 按依赖启动。已有平台不要因此重建数据库卷。
