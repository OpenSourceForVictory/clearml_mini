# CS 仿真客户端接入协议 v1

平台管理客户端：资源调度、容器、清单、日志、指标和产物。外部推理服务负责模型加载和推理，平台不托管 checkpoint。本协议不绑定 RoboTwin，也不替客户端实现某个模型服务的 HTTP/WebSocket 消息格式。

## 用户提交清单

页面进入 **CS 评测批次**，选择客户端模板及资源池、填写推理服务地址，再逐行填写任务或导入 JSON：

```json
[
  {"case_id":"pick-001","task_name":"pick_cube","episodes":20,"seed":42,"parameters":{}},
  {"case_id":"stack-001","task_name":"stack_blocks","episodes":10,"seed":42,"parameters":{}}
]
```

示例任务名仅示意，须由所选客户端真实支持。`case_id` 在批次内唯一；每项是“一个仿真任务的一组回合”，分别排队、运行、取消和重试。失败项重试不重跑成功项；本版不支持回合内断点续跑。批次独立使用 MAX_BATCH_BACKLOG_PER_USER 积压额度，默认 10000 项，超额时整体拒绝，不留下部分清单。单次最多 100 项，导入文件最多 1 MiB，每项参数最多 16 KiB。

批次显示计划项数、成功项数、已结束项数、回合总数和每项状态；只有全部成功，批次才算成功。失败、取消和超时不会计入成功数量。点击每项“详情 / 重试”查看日志、尝试、配置快照与产物。

API 示例（需要登录 Bearer Token）：

```json
POST /api/v1/batches
Idempotency-Key: my-evaluation-001

{
  "title":"模型服务回归评测",
  "pool_id":"资源池 ID",
  "gpu_mode":"exclusive",
  "client":{
    "template_id":"客户端模板 ID",
    "endpoint":"http://192.168.137.223:19090/infer",
    "request_timeout_seconds":10,
    "timeout_seconds":300
  },
  "cases":[
    {"case_id":"demo-001","task_name":"demo","episodes":3,"seed":42}
  ]
}
```

同一用户、同一幂等键返回原批次，避免网络重试重复创建。`GET /api/v1/batches` 列出自己的批次，`GET /api/v1/batches/{id}` 获取逐项进度；子任务继续使用 `/jobs/{id}`、`/cancel`、`/retry`。

单项也能 `POST /api/v1/jobs`，`benchmark=cs-client`，提交 `episodes` 和 `client`（包含 `task_name`），无需 `model_ref`。

## 管理员登记客户端

在管理设置的客户端模板区域登记名称、版本、镜像、启动参数 JSON 数组及支持协议，或使用 `POST /api/v1/admin/client-templates`：

```json
{
  "name":"CS 协议自检",
  "version":"1",
  "image":"mini-eval-client:cs-v1",
  "command":["python","-m","runner.cs_demo"],
  "protocols":["http"]
}
```

每个名称/版本唯一，模板只允许启用/停用；修改镜像或命令需要新版本。提交时复制不可变快照，停用模板不改变已提交任务。生产镜像建议使用 digest，标签须由管理员保证不可变。

镜像需要 Python 3.11+ 及本项目 `runner/` 包，再安装自己的仿真代码、依赖及资产。可参考 `runner/Dockerfile.cs-client`；示例仅使用标准库。将镜像预先构建/分发到 Worker，在配置文件增加：

```json
{"client_images":["mini-eval-client:cs-v1"]}
```

重启 Worker 后会以协议 v3 登记能力。平台只向声明对应镜像的 Worker 分配 CS 任务，旧 Worker 仍可运行旧评测类型。

## 容器协议

入口为 `python -m runner.supervise --timeout N python -m runner.cs_entry`。平台通过 Docker 复制输入文件，客户端从环境变量读取：

- `MINI_EVAL_INPUT=/tmp/mini-eval-job.json`：包含 `contract_version`、`job_id`、`execution_id`、`episodes`、`endpoint`、`task_name`、`seed`、`parameters`、超时及模板快照。
- `MINI_EVAL_OUTPUT_DIR=/tmp/mini-eval-output`：报告、图像、短视频等产物写在这里。

客户端通过 stdout/stderr 输出日志（仍受 256 KiB 日志上限限制），成功结束前写一行：

```json
{"final":{"success_rate":1.0,"episode_count":3,"runtime":2.5}}
```

必须退出码为 0、指标有限且通过 API 校验、回合数等于计划回合数，任务才算成功。`runtime` 单位为秒。客户端应遵守 `request_timeout_seconds`；平台入口和调度器另外强制总时长，避免不响应的客户端永久占用资源。

客户端网络为 Docker bridge，不使用 host 或特权模式；仅挂载管理员配置的公共只读目录及本次执行的独立可写目录；需使用从容器可达的服务地址，不能填写调用者电脑的 localhost。支持 http/https/ws/wss 地址，但应用层消息格式由客户端实现；示例只实现 HTTP。入口做容器内 DNS、TCP（HTTPS/WSS 还验证 TLS）连接预检，不等同于模型已就绪检查。拒绝回环、链路本地、未指定及组播地址，允许业务内网地址；不承诺 bridge 是严格端点出站白名单。

URL 不接受用户名/密码、查询串和片段。集中服务密钥管理尚未实现，真实服务鉴权由客户端自身集成；不要把密码放入任务参数或打印日志。

## 产物

退出后 Worker 收集输出目录中的普通文件，上传到对应 Run；不接受符号链接、硬链接、设备文件和 `..` 路径。文件名使用英文字母、数字、下划线、短横线和点，可包含子目录。

单文件最大 8 MiB，最多 8 个文件，每次尝试合计最大 32 MiB。产物包含 SHA256，重传相同文件幂等，相同名称不同内容被拒绝。产物二进制存入 PostgreSQL，现有数据库备份同时覆盖产物；大文件/长视频对象存储暂不包含在 v1。

仅任务所有者或管理员可列出/下载：`GET /api/v1/jobs/{job_id}/attempts/{run_id}/artifacts` 及 `/{artifact_id}`。下载作为附件，页面不执行 HTML 产物。不同重试独立保存产物。

## 可重复协议自检

构建 `docker build -f runner/Dockerfile.cs-client -t mini-eval-client:cs-v1 .`，运行配套测试服务 `python -m scripts.cs_test_service --host 0.0.0.0 --port 19090`（仅在可信测试网络临时使用，测试后停止），填写这台机器可达的地址 `/infer`。示例任务 `demo` 根据 observation 请求 action，再生成 `report.json`。`fail` 返回服务错误，`slow` 用于请求/总超时测试。

测试结果只证明客户端协议和平台链路，不代表真实仿真或 RoboTwin 模型评测质量。

完整 SDK、集合版本、逐回合结果和代码构建流程见 [仿真评测框架](evaluation-framework.md)。
