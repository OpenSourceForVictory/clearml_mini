# 平台业务接入包

从平台“用户指南 → 业务接入”下载 ZIP 并解压。在解压目录用 Python 3.11+ 执行：

```
python self_test.py
```

不需平台账号、GPU、模型、网络或 pip 依赖。自检在临时目录构造平台输入，运行四个入口并校验成功/业务失败的回合和最终指标。这里的成功只证明接入协议，不代表真实模型效果。

## 注册到平台

先准备宿主机批准环境或构建镜像。Docker 可在解压目录执行 `docker build -t my-client:v1 .`，再由管理员将镜像准备到节点并登记容器能力。宿主机将本包放在批准环境的代码根目录，保证 `import runner.sdk` 可用。

在运行模板注册客户端，按下表选择模式并填写命令。Docker 内代码在 `/app`；宿主机路径按批准环境替换。模板的目录映射、参数和资源请在界面设置。

| 文件 | 模板设置 | Docker 示例命令 |
| --- | --- | --- |
| ordinary_command.py | 普通命令任务 | `["python","/app/ordinary_command.py"]` |
| command_episode.py | 评测任务，SDK + 业务命令 | `["python","/app/command_episode.py"]` |
| sdk_client.py | 评测任务，直接评测协议，启用回合上报 | `["python","-m","runner.sdk","sdk_client:Client"]` |
| direct_client.py | 评测任务，直接评测协议，启用回合上报 | `["python","/app/direct_client.py"]` |

命令包装模式每次调用只跑一个回合，SDK 负责循环。普通命令不输出评测成功率。直接协议可用任意语言实现；Python SDK 类方式复用同一协议。

## 输入与参数

平台自动设置 `MINI_EVAL_INPUT` 指向本次执行 JSON：`task` 包含 case_id/task_name/episodes/seed，`business` 包含已解析业务值，`platform.service.endpoint` 是分配服务地址（无服务时 service 为 null），`platform.listen` 是服务端监听信息，`platform.paths` 是执行目录。

在模板声明业务参数 `force_failure`（boolean，默认 false，允许覆盖）可演示业务失败；实际使用替换成自己的参数和真实判定。不要自行构造平台令牌，不要在参数和日志中放密钥。

业务程序可直接读输入，也可在模板“参数映射”把字段绑定到 CLI 或环境变量。工作/输出/资产目录来自平台配置，不写死远端机器路径。命令回合的结果写到 `MINI_EVAL_RESULT`，内容如 `{"success":true,"metrics":{"score":1},"artifacts":[]}`。

## 服务端与完整模板

`runtime_templates/` 包含 HTTP 回显服务、客户端和每回合程序；`catalog.json` 包含六个完整客户端/服务端模板。它们用于测试服务端口、地址传递和目录映射。

在解压目录执行 `docker build -f Dockerfile.templates -t mini-eval-template-examples:v1 .`，准备到节点；在模板注册页载入对应样例，修改业务需要的资源要求后注册。宿主机可将 runtime_templates 三个程序放到批准的逻辑资产 demo_code，环境名称 template_demo 需在节点登记。

创建包含 echo_a/echo_b/echo_c 的集合，每项两回合；在向导选择客户端模板，服务来源选平台启动并选择回显服务模板，设置资源池和并发。服务端从 platform.listen 读监听端口，客户端从 platform.service.endpoint 获取地址。预期六回合结束且 echo_verified=1。共享/独占和资源不足排队由平台处理。

具体步骤也可随时在平台“用户指南”阅读，不需要访问源码仓库。

### 回合产物目录

配套 API 和 Worker/Runner 0.21.12 支持回合目录归属。原生 SDK 在 `run_episode` 中使用 `context.episode_output_dir / 'result.json'` 写文件，会懒创建目录并登记当前回合；`context.output_dir` 保留公共目录语义。目录内的文件可按回合查看，不强制逐文件返回 `artifacts`；显式返回的 `artifacts` 引用优先。嵌套 SDK 保持父回合编号，不额外嵌套目录。不读取该属性的旧业务不增加空目录或元数据。目录登记失败不改变业务结果，未登记文件仍可按任务浏览。

`.mini-eval-artifacts` 是平台隐藏元数据，不应作为业务产物编辑或引用；它不授予目录权限。旧 API 不会自动显示新归属；先更新 API，再更新实际 Runner/SDK。单进程直接协议仍由业务显式声明文件归属，平台不按文件夹名称推测。
