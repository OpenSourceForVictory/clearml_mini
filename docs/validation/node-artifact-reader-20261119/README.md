# 节点产物只读基础验证（2026-11-19）

## 已实施

`worker/artifact_reader.py`：相对路径、逐层 no-follow、O_PATH 固定普通文件后重开、范围读取、100 项分页、目录和文件版本校验、私有协议目录排除、明确错误状态。

`worker/artifact_access.py`：批准根及账号快照、批准版本/身份重验、隔离 Python 子进程、最小环境、超时。该模块尚未接入 Worker 主循环/API；注册仅返回可信描述，没有新增数据库表或迁移。未升级或部署节点，仍未关闭旧上传行为。

## 最终验证

```bash
MINI_EVAL_TEST_ARTIFACT_DOCKER=1 \
PYTEST_ADDOPTS='--import-mode=importlib -k "artifact_reader or artifact_media or sdk_output_directories or coverage_reporting"' \
.venv/bin/python -m scripts.test_coverage \
  --output coverage-artifacts/node-artifact-reader-verified-20261119
```

- 108 passed，2095 deselected，1 条既有 Starlette/AnyIO 弃用警告，14.79 秒。没有全量测试声明。
- artifact_reader：141/141 行、58/58 分支；artifact_access：54/54 行、18/18 分支，均 100%。真实子进程未插桩，不合并进覆盖率；同功能在测试进程内另行覆盖。
- 两个新模块及 artifact_media 纳入业务执行与结果功能组（分母由 38 增至 41 文件），没有降低阈值或排除业务代码。定向测试下目录/功能组门槛未通过，命令退出 1；完整证据见 coverage.md。
- 实际普通账号（非 root）启动子进程：可读文件成功、无权限文件拒绝、父目录只有穿越权限仍可读取批准文件、目录没有读取权限拒绝列表操作。
- 真实本机 Docker：使用已有 mini-eval-runner:dev，不拉取、不联网；显式使用当前 UID/GID，通过已有 workspace 布局挂载输出，生成 2 MiB 文件；`--rm` 确认容器删除后，分块读取并核对 SHA-256 相同。只清理本测试 UUID 容器，不改运行中业务。
- 这是本地文件通道测试，不是平台批次端到端、模型评测、GPU/NPU 测试；容器使用同 UID，不能宣称任意镜像 UID/user namespace 已通过。
- Ruff、OpenSpec strict 和 git diff --check 通过。

## 失败证据与修复

1. 首轮纯单测 44 通过、1 失败：目录快速创建文件时，单用目录时间戳没有识别变化。增加名称/inode 摘要和前后扫描校验后通过。原断言：预期 file_changed，实际 ready（test_directory_changes_and_os_errors）。
2. 首轮统一验证 107 通过、1 失败：真实 Docker 用例漏掉 workspace.prepare，execution_dirs 前 runs 父目录不存在。保留 first-run-tests.xml；修正测试按实际初始化顺序执行。
3. 后续单文件验证 50 通过、1 失败：Docker 返回 `error: no such object`，测试仅接受大写 `No such object`。统一转小写比较后通过，未放宽容器确已删除的断言。

剩余工作见 OpenSpec task-result-artifacts 的 3.1/3.2 及关联任务；本次只勾选独立 helper 基础 3.0。接口契约和接线注意事项见 [节点读取说明](../../node-artifact-reading.md)。
