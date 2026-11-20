# 把已有业务程序接入 mini-eval

本包从平台“用户指南 → 业务接入”下载。它包含运行模板样例、输入格式、四种客户端样例和离线自检。先在解压目录运行：

```bash
python3 self_test.py
```

自检不需要平台账号、GPU、模型、网络或 pip 依赖。它会运行普通命令、SDK 命令、Python SDK 和直接协议样例，并验证成功与业务失败两种结果。通过只说明本地输入输出格式正确，**不代表你的模型、仿真器或真实评测已通过**。

## 先选择接入方式

下面四项是客户端接入方式。宿主机/Docker 是“程序在哪里运行”，任务类型是“平台按什么规则判定完成”，接入方式是“业务如何给平台结果”；这些选择相互独立。

| 方式 | 适合什么程序 | 模板与命令 | 平台得到什么 |
| --- | --- | --- | --- |
| 普通命令 | 已有脚本或二进制，只需运行并看退出状态 | 客户端模板选择“普通命令”，例如 `python /app/ordinary_command.py` | 不要求 SDK 或评测协议；退出码 0 表示程序正常结束，不产生评测成功率或协议回合结果 |
| SDK + 业务命令 | 已有命令行程序，可改为每次处理一个回合并写结果文件 | 评测任务；接入方式选“SDK + 业务命令”；命令如 `python /app/command_episode.py` | 平台适配器循环启动业务命令；每次读一个回合参数，并从 `MINI_EVAL_RESULT` 读取结果 |
| Python SDK | Python 业务程序，想直接实现一个 `run_episode` | 评测任务；直接评测协议；启用逐回合上报；`python -m runner.sdk sdk_client:Client` | Runner 循环调用 `run_episode`，检查 `EpisodeResult` 并上报回合和最终摘要 |
| 直接协议 | 非 Python、已有自定义进程循环或想自行控制整次执行 | 评测任务；直接评测协议；启动自己的程序 | 读取 `MINI_EVAL_INPUT`，向 stdout 输出协议事件行和唯一的 final JSON |

新手推荐先跑 Python SDK 样例，再替换 `run_episode` 中的业务逻辑。若程序暂时不能改造，选普通命令；需要平台统计业务成功率时，退出码本身不够，选择 SDK 命令、Python SDK 或直接协议。

## 在界面注册和试跑

1. 在“管理设置 → 运行模板管理 → 注册新模板”选择客户端。选择“宿主机”或“Docker”；两者都能使用上述接入方式。
2. 评测任务选 `task_type=evaluation`（界面默认）；普通命令选“普通命令”。普通命令不接受评测协议结果字段。
3. 填名称、版本和命令。命令按“可执行程序 + 参数数组”执行，不会自动解释 `|`、`>` 或 Shell 展开。需要 Shell 语法时显式使用 `bash -lc`，并把整段脚本作为一个参数。
4. 在“业务参数”定义参数名、类型、默认值和是否允许向导覆盖；在“动态参数映射”把业务字段绑定到 argv 或环境变量。平台会将集合任务的参数合并到 `business` 输入中。
5. 保存并发布模板，将它加入任务集合允许使用的客户端模板。用一个任务、1–2 个回合先试跑，确认日志、退出码、协议结果、服务地址和产物后再扩大任务量。

Docker 模板的 Python 命令使用容器内解释器。可参考本包 `Dockerfile`；先构建镜像，再由管理员按节点容器环境流程准备/批准镜像。宿主机模板使用节点已批准的 Python 环境；把程序放入批准代码根目录或配置逻辑资产，不要依赖某台机器的临时路径。平台不会替业务安装 Python 包、下载模型或准备仿真资产。

## 平台输入文件

Worker 在每次执行前写入 JSON，并设置 `MINI_EVAL_INPUT` 环境变量指向该文件。建议直接读取它，避免把节点绝对路径写进模板：

```python
import json, os
from pathlib import Path

config = json.loads(Path(os.environ["MINI_EVAL_INPUT"]).read_text())
task = config["task"]                 # case_id, task_name, episodes, seed
business = config["business"]         # 集合值与允许覆盖值合并后的业务参数
platform = config["platform"]
endpoint = (platform["service"] or {}).get("endpoint")
output_dir = Path(platform["paths"]["output"])
```

`platform.service` 在未分配服务时为 `null`；服务地址由平台按任务分配，程序应读取它，不要写死服务节点 IP。服务端模板从 `platform.listen.address` 和 `platform.listen.port` 获取监听信息。`platform.paths` 提供 work、output、cache 和逻辑 assets 路径。容器中这些是容器可见路径；宿主机是批准环境中的路径。SDK 还提供 `context.service_endpoint`、`context.business`、`context.work_dir`、`context.output_dir` 和 `context.assets_dir`。

模板的参数映射适用于希望通过命令行或环境变量接收平台字段的程序；此时可不读取整个 JSON。查看批次的“运行配置”可核对业务参数最终值、映射目标和启动命令预览。逐回合启动预览中的回合 ID、种子和输出路径会随回合变化。

## Python SDK：实现业务回合

`sdk_client.py` 是完整最小例子。把 `success` 判断替换为真实评测，异常按业务需要处理；`success` 必须是 Python `bool`，metrics 只能包含有限数值：

```python
from runner.sdk import EvaluationClient, EpisodeResult

class Client(EvaluationClient):
    def setup(self, context):
        self.endpoint = context.service_endpoint

    def run_episode(self, task, episode, context):
        # 在这里调用仿真器/模型服务，并根据真实断言判断结果。
        # 可运行的协议演示；替换为你的模型/仿真调用和真实断言。
        passed = not context.business.get("force_failure", False)
        report = context.episode_output_dir / "result.json"
        report.write_text('{"done": true}')
        return EpisodeResult(success=bool(passed), metrics={"seed": episode.seed})
```

`setup(context)` 和 `teardown()` 分别在整项任务开始/结束调用；Runner 对每个回合调用 `run_episode(task, episode, context)`。回合 ID 从 1 开始，种子为 `(seed + episode_id - 1) % 2147483648`。SDK 发 `STARTED`、`COMPLETED` / `ERROR` 事件和唯一的最终摘要；业务成功率与程序退出状态分别显示。代码异常会使执行失败；业务判定失败应返回 `EpisodeResult(success=False, ...)`，这样回合可正常结束并计入评测失败。

平台 Python SDK 随 Worker/Runner 组件包提供。Docker 镜像中要有兼容的 Python 运行环境；平台在启动时注入 Runner。宿主机 Python 由已批准环境决定。不要把 Worker 仓库路径或本机令牌拷进业务程序。

## SDK + 业务命令：每次处理一个回合

命令适配器由平台负责循环，每次业务进程只处理一个回合。业务程序从 `MINI_EVAL_INPUT` 读任务和业务参数，并向 `MINI_EVAL_RESULT` 指向的文件写一个 JSON：

```json
{"success": true, "metrics": {"score": 0.98}, "artifacts": ["reports/episode.json"]}
```

`success` 必须为布尔值；`metrics` 是有限数值字典；`artifacts` 为输出目录下相对文件名列表。每次调用还可从 `MINI_EVAL_EPISODE_ID` 与 `MINI_EVAL_EPISODE_SEED` 读取当前回合。不要在业务命令里再次循环全部回合，否则会重复执行或结果无法对应。参考 `command_episode.py`，模板命令形如 `python /app/command_episode.py`，接入方式选“SDK + 业务命令”。

## 直接协议：任意语言的输入与 stdout

程序读取 `MINI_EVAL_INPUT` 指向的 UTF-8 JSON。每个回合一行 `MINI_EVAL_EVENT ` 加 JSON；普通日志可继续写 stdout/stderr。协议事件示例：

```text
MINI_EVAL_EVENT {"episode_id":1,"seed":42,"status":"STARTED"}
MINI_EVAL_EVENT {"episode_id":1,"seed":42,"status":"COMPLETED","success":true,"metrics":{"score":0.98},"artifacts":["episode-1/result.json"],"runtime":1.25}
{"final":{"success_rate":1.0,"episode_count":1,"runtime":1.30}}
```

每个计划回合必须有对应事件；成功或业务失败用 `COMPLETED`，并以布尔 `success` 表达；执行异常用 `ERROR`。最后必须恰好输出一行无前缀 `final` JSON，`episode_count` 必须等于计划回合数，`success_rate` 为真实比例，`runtime` 单位为秒。只退出 0 或只打印普通日志，不足以构成有效评测结果。当前直接协议默认由一个进程处理整个任务；只有宿主机模板选“每回合启动一次”时，程序每次收到 `episodes=1` 的单回合输入并输出单回合 final，由平台汇总。

Python SDK 是 Runner 对同一事件协议的实现；直接协议样例见 `direct_client.py`。HTTP/WS/WebSocket 等业务服务消息格式由你的程序实现，平台只负责分配服务地址并将其传给客户端，不会自动把一种应用协议转换成另一种。

## 服务端模板样例

`runtime_templates/` 提供 HTTP 回显服务、客户端和 SDK 命令样例；`catalog.json` 提供可载入的客户端/服务端模板。可在解压目录构建：

```bash
docker build -f Dockerfile.templates -t mini-eval-template-examples:v1 .
```

之后由管理员将镜像准备到节点并登记容器能力，再从模板页载入样例。创建 `echo_a`、`echo_b`、`echo_c` 三个任务，每项两回合，选择回显服务模板并试跑。客户端从 `platform.service.endpoint` 访问服务；服务端读取 `platform.listen`。这个回显样例只验证启动、地址传递和连通，不是模型推理验收。

## 常见问题

- **进程退出码是 0，但评测结果失败/缺失**：评测任务必须按所选协议返回有效业务结果；退出码只描述进程是否正常结束。
- **回合只显示正在运行**：普通整任务命令不产生协议回合状态；直接协议需输出事件，SDK 需返回 `EpisodeResult`，SDK 命令需写 `MINI_EVAL_RESULT`。
- **服务地址为空或不可达**：确认向导选择了平台服务或外部 endpoint，并读取输入中的实际 endpoint；不能用客户端节点上的 `localhost` 指代服务端。
- **模板命令没有带业务参数**：检查参数已在模板 schema 定义、映射到 argv/env，且集合字段名与映射字段完全一致；在预检/运行配置查看最终值。
- **样例自检成功但真实程序失败**：本地自检不含你的依赖、设备、模型权重、资产、网络策略或服务应用协议；从具体执行日志继续检查这些现场条件。

更完整的参数契约、UI 步骤和服务编排可在平台“用户指南”查看。无需访问源码仓库。

### 回合产物目录

配套 API 和 Worker/Runner 0.21.12 支持回合目录归属。原生 SDK 在 `run_episode` 中使用 `context.episode_output_dir / 'result.json'` 写文件，会懒创建目录并登记当前回合；`context.output_dir` 保留公共目录语义。目录内的文件可按回合查看，不强制逐文件返回 `artifacts`；显式返回的 `artifacts` 引用优先。嵌套 SDK 保持父回合编号，不额外嵌套目录。不读取该属性的旧业务不增加空目录或元数据。目录登记失败不改变业务结果，未登记文件仍可按任务浏览。

`.mini-eval-artifacts` 是平台隐藏元数据，不应作为业务产物编辑或引用；它不授予目录权限。旧 API 不会自动显示新归属；先更新 API，再更新实际 Runner/SDK。单进程直接协议仍由业务显式声明文件归属，平台不按文件夹名称推测。
