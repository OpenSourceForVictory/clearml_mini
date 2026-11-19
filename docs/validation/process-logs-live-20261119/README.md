# 独立日志真机实验（2026-11-19）

基于 `03b2851` 工作树，使用当前 Worker 0.21.7 / 协议 17 和 Runner 源码。没有改业务实现。测试 API 为本机隔离端口 18090，PostgreSQL 为一次性容器，迁移至当前 head `z629c01`；独立账号、节点登记、资源池和工作目录。没有升级生产 API、生产数据库或已纳管节点。

## 完整平台链路

本机 RTX 4090，实际 GPU Worker 注册、平台分配、systemd 宿主机进程或真实 Docker 启动，每次 CUDA 初始化并申请/释放 1 MiB 显存。Docker 使用本机 `mini-eval-runner:dev` 与现有执行路径注入的当前 Runner，不是对外发布镜像更新验收。

| 模式 | 预期与实际任务状态 | 日志 |
| --- | --- | --- |
| 宿主机整任务，普通命令 | SUCCEEDED | 1 份 business 公共日志 |
| 宿主机逐回合，普通命令，第二回合退出 7，continue | FAILED；第三回合仍执行 | 3 份 episode 日志 |
| 宿主机逐回合，直接评测协议 | SUCCEEDED | 3 份 episode 日志 |
| Docker 整任务，普通命令 | SUCCEEDED | 1 份 business 公共日志 |
| Docker SDK 命令适配 | SUCCEEDED | 3 份 episode 日志 |

共 5 个任务、11 个业务日志 scope。逐项检查目录真实存在、scope closed、stdout/stderr、中文 UTF-8、CUDA 完成标记；逐回合检查不存在其他回合的输出标记。通过真实 API 请求邮箱让 Worker 读取正文，再单独请求 stderr 过滤，未模拟 API 或文件。普通命令整任务提交 episodes=1；其他配置提交 3 回合。

所有任务 `cleanup_confirmed=true`。重启这个隔离 Worker 后，用新的 tail/stdout 请求读取已经结束的 Docker 第三回合，成功返回 GPU 标记。不是仅查看旧页面缓存。

证据：[平台用例输出](gpu-platform.txt)、[任务及日志记录](gpu-evidence.json)、[重启后读取](restart.txt)。

## 真实浏览器

Chromium 连接隔离 API 的真实前端，无接口 mock：我的任务 → 查看详情 → 回合 2 日志；核对第二回合正文不含第一回合标记，筛选 stderr，下载 JSONL 并解析确认所有记录来源为 stderr，最后关闭抽屉。

证据：[浏览器结果](browser.txt)、[截图](browser.png)、[实际下载文件](download-stderr.jsonl)。未在此实验中验证长日志翻页、超过下载上限或窄屏。

## 29 节点 NPU

SSH 连接 1.95.69.29，在独立临时目录上传当前采集模块。NPU 6、7 各启动一个同名 `same_service.py` 子进程，以不同 execution_id 同时采集 service scope。每个进程设置对应 `ASCEND_RT_VISIBLE_DEVICES`、使用逻辑设备 0，实际 ACL 初始化及 1 MiB 显存申请/释放。核对退出 0、日志 closed、stdout/stderr 和另一实例标记不串入。

证据：[NPU 输出](npu-services.txt)。这验证真实 NPU 子进程采集与同名实例文件隔离，**没有通过平台调度启动两个 NPU 服务，也没有验证 NPU 服务页面或真实模型推理**。业务日志临时目录自动清理；没有修改远端安装或 systemd 服务。

## 失败证据及边界

实验准备阶段遇到并纠正以下测试脚本问题，没有修改平台以绕过检查：

- `.venv/bin/python` 被 `resolve()` 解析为全局解释器，报 `No module named alembic`；改用虚拟环境绝对路径。
- PostgreSQL `--rm` 容器停止后立即重建出现短暂同名冲突；待删除完成后重建。
- 模板创建 400：整任务不能配置 `episode_error_policy=continue`，普通命令整任务不能打开 episode_reporting；按现有契约修正测试输入。
- 提交 400：`ordinary commands require episodes=1; they do not report episodes`；整任务普通命令按现有契约使用 1，逐回合使用 3。

未运行全量测试或统计新覆盖率，未插桩真实子进程、浏览器和 Docker 不能计入 Python 覆盖率。本轮结果不替代磁盘故障、10000 回合、慢盘、多用户压力、旧镜像、线上迁移及节点在线升级验收；OpenSpec 相应复合任务保持未完成。

清理确认：五个任务均完成资源清理，隔离 Worker/API 已停止、18090 不再监听，一次性 PostgreSQL 已停止；29 上传的测试模块目录已删除。本机日志与私有测试配置保留在 `.runtime/`，不提交仓库。
