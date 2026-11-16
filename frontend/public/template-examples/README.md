# 完整运行模板样例

本样例验证平台任务、参数、服务地址和回合结果的连通性，使用 HTTP 回显服务，不代表真实模型质量评测。六份模板在 `frontend/public/template-examples/catalog.json`：客户端 Docker/宿主机 × SDK 每回合命令/直接协议，以及服务端 Docker/宿主机。注册页面可按类型预览并载入，载入后仍须注册，服务不会自动启动。

## 1. 准备环境

Docker：在仓库根目录执行：

```bash
docker build -f examples/runtime_templates/Dockerfile -t mini-eval-template-examples:v1 .
```

将镜像同步到可执行节点，并按平台运行镜像策略批准该镜像。构建需要基础 Python 镜像；可通过 `--build-arg BASE_IMAGE=已有兼容Python镜像` 指定。

宿主机：将本目录三个 Python 程序放在节点同一目录，登记为只读资产 `demo_code`；批准宿主机环境 `template_demo`，其中须有 Python 3.11+，并能导入本仓库 `runner` 包（通过批准环境的代码路径配置）。在机器管理完成环境探测。模板中的 `${assets:demo_code}` 由节点解析，不填写另一台机器的真实目录。

## 2. 注册服务端和客户端

管理设置 → 运行模板管理 → 服务端模板 → 注册新模板，从样例选择 Docker 或宿主机 HTTP 回显服务。检查名称、版本、镜像或环境名称，再注册。

客户端同样注册一个版本：SDK 命令每回合运行 episode.py，direct 模式运行 client.py 并由 runner.sdk 输出回合协议。客户端业务字段 `scene` 默认 room；服务端 `model` 默认 echo-demo。程序都从 MINI_EVAL_INPUT 读取标准输入，不需要手写管理凭据。

## 3. 提交三任务演示

在任务集合中创建三个任务，任务名分别 echo_a、echo_b、echo_c，每项两回合、种子 1，业务参数 scene 分别填写 room、table、lab。任务名只是演示标签，不是 RoboTwin 任务。

任务提交向导选择该集合和客户端模板；服务端选择平台启动，使用已注册回显模板及 model=echo-demo。根据资源池设置资源申请和并行数。选择共享服务时多个客户端连接该服务；选择独占时按平台绑定关系隔离。也可先部署服务实例，再在向导填写实际可达 HTTP 地址。不要在模板中写固定推理服务地址。

预期：3 项任务、6 个回合完成；每回合 echo_verified=1、success=true。服务端实际收到任务名、scene 与回合编号。改错地址应出现连接失败；改 model 值会产生评测失败结果，不应伪造成功。

## 4. 字段与挂载示例

运行模板的“业务命令参数映射”可选择平台任务名→--task、推理服务地址→--server-url，或业务字段 scene→--scene。程序必须解析对应参数；本配套程序直接读输入 JSON，因此样例默认不重复配置 argv。

“目录挂载”统一管理容器目录。来源选节点资产，再填写已登记的逻辑名和容器目标；额外目录必须经节点批准。工作和输出目录由平台按执行隔离。旧模板 assets 声明保留以兼容原 `${assets:name}` 引用。

## 5. 使用自己的程序

将 echo 请求替换成仿真逻辑，success 必须来自真实业务判定。SDK 命令向 MINI_EVAL_RESULT 写入结果；direct 程序可继承 EvaluationClient。服务端读取 platform.listen，客户端读取 platform.service.endpoint。RoboTwin/Cosmos 等需要资产和模型的示例见 examples/cosmos_robotwin，不要把本回显样例当作真实 RoboTwin 评测。
