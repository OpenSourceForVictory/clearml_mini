# 仿真评测框架

平台管理评测集合、运行、子任务与执行尝试；用户基于 SDK 实现单回合仿真。推理服务仍由外部维护。

## 集合与运行

在 CS 页面选择客户端模板，填写或导入清单，填写集合名称和版本后点击“保存集合版本”。同名版本不可覆盖；修改需新版本。集合归用户所有。选择已保存集合后可多次提交运行，每次填写推理地址、资源池、显存和最大并发。所有子任务先入库，资源不足保留排队。

每次运行最多 100 项，每项最多 10000 回合。`MAX_BATCH_BACKLOG_PER_USER` 默认 10000 个未结束子任务，与单独提交任务的 `MAX_PENDING_PER_USER` 分开；超过积压上限整个提交拒绝，不产生半份清单。共享显存是准入预留，并非硬隔离；GPU 利用率为池级负载参考，不提供百分比算力保证。

调度按用户轮询，同一用户内按集合轮询；普通独立任务作为一个组保持 FIFO。运行 max_concurrent 默认 1、最大 100。预留、启动、运行、取消中以及尚未确认清理的执行均占额度。额度与空闲资源共同决定实际并行数。一次运行结束一个子任务并释放资源后，自动推进后续任务。

## SDK 接入

```python
from runner.sdk import EvaluationClient, EpisodeResult

class Client(EvaluationClient):
    def setup(self, context):
        self.endpoint = context.config['endpoint']
        # 初始化仿真环境及推理连接，遵守 request_timeout_seconds。

    def run_episode(self, task, episode, context):
        # episode.id 从 1 开始；episode.seed 可用于环境随机种子。
        # task 包含 task_name、parameters、endpoint 等配置。
        context.report('simulating')
        return EpisodeResult(success=True, metrics={'reward': 1.0})

    def teardown(self):
        pass
```

基础镜像构建：

```bash
docker build -f runner/Dockerfile.base -t mini-eval-client-base:1 .
```

派生镜像：

```dockerfile
FROM mini-eval-client-base:1
WORKDIR /app/client
COPY client.py ./
ENV PYTHONPATH=/app:/app/client
```

模板命令是 `["python", "-m", "runner.sdk", "client:Client"]`，勾选 `episode_reporting`。HTTP 自检模板可直接使用基础镜像和 `runner.sdk_demo:DemoClient`，它验证通信与平台协议，不代表 RoboTwin 模型评测。自定义镜像需安装对应仿真依赖和资产，并由管理员分发到 Worker，加入 `client_images` 白名单。

## 回合事件与部分结果

计划由任务快照的 episodes 和 seed 确定。回合 i 的种子为 `(seed+i-1) % 2147483648`。SDK 接管循环并输出 `MINI_EVAL_EVENT ` 前缀的 JSON，用户不应自行输出这个保留前缀。Worker 用独立持久化游标转发，日志截断不会截断进度。事件 API 按 Worker 执行凭据鉴权，客户端容器没有平台令牌。

每回合先 STARTED，随后 COMPLETED 或 ERROR。COMPLETED 的 success=false 表示评测有效完成但目标失败；ERROR 表示执行异常，不计完成。事件重复内容相同则幂等，内容冲突拒绝。每个执行尝试独立保存数据；重试整个任务不会把旧尝试的结果加到新尝试上。

用户通过运行列表查看已完成/计划、目标成功数，再点“回合结果”查看分页指标；接口为 `GET /api/v1/jobs/{job_id}/attempts/{run_id}/episodes?page=1`，每页 100 条。失败和取消后已上报结果保留。尚未结束但没有后续事件的回合仅表明“已开始”，是否中断结合任务终态判断。平台不支持回合中途恢复或只重跑缺失回合。

SDK 客户端成功收尾必须有全部计划回合的完成记录，且最终成功率与记录一致。普通命令客户端可继续使用，但不承诺逐回合进度。自定义指标只保存逐回合有限数值（最多 64 个），不自动把不同指标取平均。产物仍遵守每次最多 8 文件、每文件 8 MiB、总计 32 MiB；引用文件不等于已上传，文件在退出后回收。

## 用户代码提交与构建

CS 页“提交仿真客户端代码”接受 Python、requirements.txt、JSON/YAML 等文本文件；API `POST /api/v1/client-builds` 接受 `{name,version,entrypoint,files}`，files 为相对路径到文本的映射，支持包目录。代码包最多 100 文件/1 MiB，单用户最多 5 个未完成构建。仿真大资产通过派生基础镜像或用户镜像提供，不放入代码包。禁止覆盖平台 runner、Dockerfile 或路径穿越。源码 SHA256、版本、状态和最终构建日志持久化；构建失败不登记模板。

构建程序必须在专用 Docker 构建主机执行，与 API 服务分离；依赖安装本质上会执行用户代码，普通 Docker 构建不是不可信代码的安全沙箱。不要在存有生产密钥或评测工作负载的共享宿主机开放任意用户构建。API 不持有 Docker socket。

```bash
# ADMIN_API_TOKEN 由构建运维环境注入，不传入构建容器。
python -m scripts.build_client --api http://api:18000 \
  --base-image registry/mini-eval-client-base:1 \
  --image-prefix registry/mini-eval-user --push
```

每次调用领取一个构建，适合在专用主机由 systemd 循环调用。服务端构建租约 30 分钟，执行构建最长 25 分钟，推送最长 2 分钟；超时不发布客户端。重试失败构建使用新版本。成功会创建归提交用户所有的 SDK 客户端模板。成功模板需将镜像分发/拉取至执行节点并加入白名单后才可调度；基础版不自动更改生产 Worker 白名单。页面展示最终构建日志，当前不提供流式构建日志。

## Worker 固定工作目录

每个 Worker 配置 `workspace_root` 为管理员指定的绝对路径，例如 `/home/huawei/mini-eval-workspaces/gpu0`。不能含符号链接或 Docker mount 分隔符。默认在配置旁建立 `workspace-<配置名>`；旧 `state_path` 保留兼容，迁移已有执行状态时不要直接移动正在使用的状态目录。

```text
workspace_root/
  state/                       Worker 状态（仅宿主机）
  assets/                      管理员预置仿真资产，只读挂载
  cache/                       管理员预置公共缓存，只读挂载
  runs/<execution_id>/
    input.json                 本次配置快照（不挂载）
    work/                      客户端可写临时数据及 episodes.jsonl
    output/                    客户端报告、轨迹等结果
```

SDK 提供 `context.work_dir`、`assets_dir`、`cache_dir`、`output_dir`。需要可写模型/仿真缓存时放在本次 work 目录；公共 cache 不允许任务修改。容器仅挂载这四个指定目录，不挂载工作根目录、Worker 凭据或其他执行数据。不同任务使用独立 execution_id，重试新建目录；Worker 重连恢复同一执行目录。

基础环境由版本化容器镜像管理，Docker 镜像存储仍由 Docker daemon 管理，不会拷贝进每个工作目录。公共资产只放允许该 Worker 所有任务读取的数据，用户私有数据放各自执行目录。任务完成后容器可删除，宿主机工作数据保留；当前没有自动磁盘清理或磁盘配额，管理员应监测空间，仅在执行终止且不再需要数据时清理对应目录。

逐回合事件在独立的 episodes.jsonl 中持久化，Worker 按字节游标转发，不依赖 Docker 文本日志是否轮转。异常链接、截断和超大事件会被拒绝，已有上报结果仍保留。

Worker 可配置 `min_free_disk_gb`（默认 2）。每次心跳上报工作磁盘空间，低于阈值或磁盘遥测超过 15 秒不准入新任务；启动前再次检查。计算资源页展示可用空间和阈值，空间恢复后自动继续调度。该机制不预留每任务磁盘额度，不能阻止运行中数据增长或外部程序占用磁盘；写满导致的客户端异常会保留已上报结果。Docker 镜像所在分区若与工作目录不同，需要运维单独监测。当前不自动清理数据。
