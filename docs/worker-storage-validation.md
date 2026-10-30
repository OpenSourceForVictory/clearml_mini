# Worker 存储配置验收（2026-10-30）

`worker-storage-settings` 实现完成。使用独立 PostgreSQL/API 与独立 Worker，在本机（192.168.137.71）和 R8000（192.168.137.223）验证；独立验收阶段未升级现有平台，后续正式上线结果见文末。机器上以虚拟 GPU 身份运行真实 Docker 容器，验证的是存储契约与 SDK 上报，不是机器人模型质量或真实 GPU 算力调度。

## 自动化验证

- 后端、Worker、RoboTwin 集合/队列协议与 PostgreSQL 集成测试：**79 passed**，包含真实挂载探测及不存在镜像的失败路径。
- 浏览器：存储表单权限、草稿保留、版本冲突、失败/离线/部分生效提示、恢复本机配置，以及 CS 子页签回归，**4 passed**。
- TypeScript 检查及 Vite 构建通过。
- PostgreSQL 16.8 `upgrade head`、`alembic check` 通过；在空闲测试库降级至 `b285c07`，插入旧机器记录再升级，确认默认 revision=0/config=null。
- PostgreSQL 并发保存与领取：两个相同 expected_revision 的保存只有一个成功；任务要么保留已领取的资源和执行身份，要么返回队列并释放预留。
- OpenSpec strict 校验与 `git diff --check` 通过。

## 两台真实主机验收

完整证据：[worker-storage-20261030.json](validation/worker-storage-20261030.json)。

| 阶段 | 任务 / 回合 | 验证结果 |
| --- | --- | --- |
| 原目录运行中提交新配置 | 2 / 4 | 两台分别上报 waiting；本机测试 Worker 被 SIGKILL 后恢复，run_id 不变、无新增 attempt，两个任务继续读原资产标记并成功 |
| 空闲切换至新目录 | 2 / 4 | 两台分别确认 revision=1；各用不同宿主目录，容器内路径一致；Docker inspect 确认 assets/cache 只读、work/output 可写；读取新资产标记并成功 |
| 恢复本机初始配置 | 2 / 4 | 两台分别确认 revision=2；恢复原目录，读取原资产标记并成功 |

每阶段确实分配到两个不同槽位，共 **6/6 SUCCEEDED、12/12 回合完成且成功**。每回合上传挂载路径/资产标记 JSON，证据包含逐卡报告、实际 bind mounts、run_id、回合汇总和附件内容。原目录和新目录的数据均保留。

最终成功运行目录：本机 `.runtime/storage-19abb708d5`，R8000 `/home/huawei/mini-eval-storage-19abb708d5`。测试 Worker、临时 SSH 隧道与测试 HTTP 服务均停止，测试机器凭据禁用；现有平台服务保持运行。两台单卡主机的验收不能替代真实多卡主机验收；同机双槽位目录隔离由自动化测试覆盖。

## 验证过程中的修正

首次样本因模块复制位置和不可解析的测试端点失败；修正为 `/app/storage_check.py`，同步框架代码，并使用独立 TCP 测试服务。随后一轮双机任务成功，但更严格的分布断言及并发回归受共享测试数据库影响；回归数据库与双机 API 数据库分离，并延长样本以观察两台同时执行，最终上述验证全部通过。早期记录仍保留在 `.runtime/storage-e8d2e20a70`、`storage-424cf8a9e0`、`storage-2b1667119a` 对应日志中。

## 复现

先构建 [存储验收镜像](../examples/storage-check/README.md)，创建专用已迁移数据库及测试管理员，在 `127.0.0.1:18090` 启动 API。通过环境变量提供测试凭据，不在终端输出 Worker token：

```bash
EVAL_API_URL=http://127.0.0.1:18090 \
STORAGE_TEST_HOST=<本机局域网地址> \
python -m scripts.smoke_storage --remote <远端用户@地址> --identity <本机私钥路径>
```

另外设置 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD`。回归测试使用另一个独立的 `TEST_DATABASE_URL`；设置 `STORAGE_PROBE_TEST_IMAGE` 启用真实 Docker 探测。不要让调度并发测试与运行中的验收 API 共用数据库，更不能指向生产数据库。

## 正式上线（2026-10-30）

已升级 R8000 上的正式 API/前端，数据库迁移至 `c396d08`。本机和 R8000 的系统服务已重新启动，均登记协议 v4、在线且原目录校验成功。未修改两台机器的工作目录或资产位置，期望配置仍为 revision=0/config=null。上线前确认无持有执行，并备份正式数据库、旧代码和旧镜像。

使用真实管理员登录现有平台的浏览器验收通过：两台机器的「存储配置」表单可见，正确显示实际目录和生效状态。原访问地址及端口保持不变，在「管理设置 → 已注册机器 → 机器名 存储配置」使用。证据见 [上线记录](validation/worker-storage-deployment-20261030.json)，页面截图保存在 `.runtime/storage-live-ui.png`。
