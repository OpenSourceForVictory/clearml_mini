# 服务就绪检查

在服务端运行模板勾选“自定义就绪检查”。健康检查配置是模板快照的一部分，修改模板需创建新版本，已提交的服务不受影响。

- TCP 只证明端口可连接。
- HTTP 可配置状态码与响应条件。模型未就绪应返回 503，或用 JSON 条件检测 `/ready` 等字段。
- WebSocket 握手只证明协议入口可用；消息模式可先发送健康消息，也可只接收服务主动发送的就绪消息。平台不会执行真实推理作为默认检查。
- 文本相等严格匹配（包括空白）；JSON Pointer `/data/ready` 配合布尔值 `true`，与字符串 `"true"`、数字 `1` 不相等。

例如 WS 服务就绪配置：

```json
{
  "version": 1,
  "kind": "websocket",
  "path": "/ws",
  "mode": "message",
  "send_text": "{\"type\":\"health\"}",
  "expect": {"type": "json_equals", "pointer": "/ready", "value": true},
  "interval": 5,
  "timeout": 2,
  "startup_timeout": 600,
  "success_threshold": 1,
  "failure_threshold": 3,
  "stale_after": 20
}
```

平台没有规定业务消息格式，示例需与用户服务提供的健康消息一致。响应不符合、连接关闭或超时均不计为成功。消息最多 64 KiB，每次最多接收 16 条，检查完成关闭连接。

## 端口与参数

业务评测向导自动从机器服务网络的端口范围分配宿主机业务端口。单独部署服务实例仍由管理员填写业务端口。健康端口默认复用业务端口；选择独立端口时，平台额外分配一个宿主机端口，并为容器映射到模板声明的内部健康端口。机器需预先配置服务端口范围。

服务程序可以读取 `MINI_EVAL_PORT`、`MINI_EVAL_BIND_HOST` 和 `MINI_EVAL_HEALTH_PORT`；也可以通过平台参数映射使用 `platform.listen.port`、`platform.listen.address`、`platform.listen.health_port`。独立健康端口受相同预留和清理规则保护，未确认停止清理前不复用。

客户端通过 `platform.service.endpoint` 或模板映射获取业务 URL。当前平台自动生成的托管服务地址为协议、地址和端口，不会自动附加业务路径。健康检查路径不会改变客户端 URL。

## 凭据与证书

认证请求头中填写环境变量引用名，例如 `Authorization` → `HEALTH_TOKEN`；值应包含服务要求的完整认证值，例如 Bearer 前缀。管理员在 Worker 的 systemd 服务环境文件中提供真实值，并在节点本地 Worker 配置添加：

```json
{"probe_environment": ["HEALTH_TOKEN", "HEALTH_CA"]}
```

修改后重启空闲 Worker 以重新上报引用能力。环境文件应仅服务账户可读；模板和平台仅保存引用名。`HEALTH_CA` 可指向节点上批准的绝对 CA 文件路径，TLS 默认校验证书。缺少或未批准引用将阻止新模板调度，引用值不传入任务输入和业务环境。

## 状态与故障处理

就绪需要达到连续成功次数；连续失败达到阈值后停止分配新客户端。报告过期视为未知。服务健康下降不会自动重启服务或终止已经运行的客户端。首次就绪等待超时则启动失败并停止、清理执行。

客户端启动前验证服务地址的网络可达性。WS 地址包含明确业务路径时验证握手；只有基础地址时仅验证传输可达性，避免业务程序自行附加路径时被误判。认证挑战 401/403 只证明地址可达，业务认证仍由客户端负责；此检查不等同于模型就绪。检查失败结束本次尝试，按已有流程确认清理，修复网络后显式重试。单个客户端节点网络故障不会覆盖服务全局健康。

## Worker 安装与回滚

新配置要求 Worker 协议 v12 和 `service_readiness_v1` 能力。探测依赖固定为 aiohttp 3.14.5、aiodns 3.6.1、async-timeout 5.0.1；Worker 启动时检查，在不可变代码目录外安装。系统 Python 没有 pip 时使用校验 SHA-256 的固定 PyPA pip 25.2 zip 应用安装到独立目录，不修改系统 Python。无外网节点应提前为 Worker 使用的 Python 准备相同依赖；安装失败时不会声明新能力，新模板保持等待并提示管理员，旧模板继续可用。

新 API 和增量迁移先上线，再升级 Worker，最后开放新模板。回滚先停止新配置提交并排空新检查执行，保留增量字段，不允许旧 Worker 将新模板降级为 TCP。已有 HTTP/TCP 模板未开启自定义检查时沿用原配置和阈值。

## 健康报告接口

Worker 在当前服务执行的有效租约内调用 `POST /api/v1/worker/executions/{execution}/service-probe-session` 获得探测代际，再向同路径的 `service-health` 上报 epoch、sequence、ready、sample_age_ms、duration_ms、phase、error_code。重复或乱序报告不刷新有效期；旧代际和旧执行被拒绝；报告序号存在缺口时重置连续计数。状态展示和调度使用相同报告有效期。

## 可运行样例

`examples/services/readiness_server.py` 同时提供 `/healthz` 与 `/ws`，默认模拟加载两秒，随后返回 `ready: true`。可用下面命令启动：

```bash
python -m pip install aiohttp==3.14.5
MINI_EVAL_PORT=8000 MODEL_LOAD_SECONDS=2 python examples/services/readiness_server.py
```

WebSocket 连接后会主动发送当前状态，也响应 `{"type":"health"}`。可分别验证“只接收”和“先发送”模式；没有依赖 RoboTwin 或评测 SDK。独立健康端口由 `MINI_EVAL_HEALTH_PORT` 指定，业务与健康监听都会启动。

安装引导采用 [pip 官方文档的 zip 应用方式](https://pip.pypa.io/en/stable/installation/)，并锁定引导版本及文件摘要。

Worker 探测依赖在后台准备，不阻塞注册心跳；依赖就绪后心跳更新 `service_readiness_v1` 能力，依赖失败仍阻止需要该能力的服务任务。节点纳管及升级版本验收不再要求此可选能力。排查和重试见 [回连超时与依赖准备](node-reenrollment.md#回连超时与可选探测依赖)。
