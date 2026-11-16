# 通用运行模板与业务协议

管理设置 → 运行模板管理，分客户端/服务端。每类下有“已注册模板”和“注册新模板”页签，切换页签保留未提交草稿；每类都有 Docker 与宿主机执行模式，版本不可变；复制新版本后修改，归档只停止新引用，旧任务保存快照。现有模板默认 Docker/direct。

客户端注册 API 为 `/api/v1/admin/client-templates`，服务端为 `/api/v1/admin/server-templates`。注册使用 POST，启停 PATCH `/{id}`，归档 DELETE `/{id}`，复制 POST `/{id}/clone` 带 name（可选）和 version。宿主机模板只能由管理员注册，使用 `host_runtime.environment_name` 指向节点批准环境，不填 image/runtime；Docker 使用 image 和 runtime。环境准备/实例部署由后续同一 OpenSpec 任务实现，模板注册不代表已经可以执行。

## SDK + 业务命令

`integration_mode: sdk-command` 表示一次业务命令只运行一回合；平台 SDK 循环调用命令。命令为 argv 数组，无 shell 展开；支持 `${task_name}`、`${episodes}`、`${seed}`、`${episode_id}`、`${episode_seed}`、`${endpoint}`、`${input_path}`、`${work_dir}`、`${output_dir}`、`${result_path}` 和 `${assets:逻辑名}`。

每回合自动设置 `MINI_EVAL_EPISODE_ID`、`MINI_EVAL_EPISODE_SEED` 和 `MINI_EVAL_RESULT`。业务程序须把结果写到 MINI_EVAL_RESULT 指定文件（不超过16KiB），例如：

```python
import json, os
from pathlib import Path
# 在这里调用仿真及推理服务；success 应由业务判定。
Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({
    'success': True, 'metrics': {'reward': 1.0}, 'artifacts': []
}))
```

SDK 校验结果并生成原有 STARTED/COMPLETED/ERROR 回合事件，最终生成 report.json 及汇总。success 必须为布尔值，评测失败 success=false 不等于程序异常；非零退出、没有结果、结果不合法会让执行失败。业务命令仍可输出普通日志，不能以零退出码替代评测结果。

## 直接协议

`integration_mode: direct` 让程序一次执行完整任务，可使用既有 `runner.sdk module:ClientClass`，也可自己实现 `runner/episode_contract.py` 事件及最终结果格式。启用 episode_reporting 时，事件写入 `MINI_EVAL_WORK_DIR/episodes.jsonl`，逐行完整 JSON；Worker 持久化游标并重试，服务端按执行与回合身份去重。不要自行携带 Worker token，业务程序不需要平台管理凭据。

两种模式读取同一 `MINI_EVAL_INPUT` JSON，使用同一工作/输出/资产目录约定。业务参数放 parameters，任务名和回合数来自集合快照。普通日志不能推断回合完成；未集成协议的程序只能显示进程日志，不能伪造逐回合进度。

## 服务端模板

服务端模板固定 direct，额外描述 port、protocol、health_type（http/tcp）、health_path、startup_timeout。启动命令可使用 `${port}`，部署实例时指定宿主机端口并由平台做冲突和资源检查。健康描述属于模板；实例管理和执行能力在同一 change 后续任务中交付。

## ClearML 参考

参考 [ClearML Agent 官方仓库](https://github.com/clearml/clearml-agent) 的任务配置与队列拉取，以及 [执行环境文档](https://clear.ml/docs/latest/docs/clearml_agent/clearml_agent_execution_env/) 的 Docker/虚拟环境分离。当前平台先接入管理员已准备的宿主机环境，不自动复制 ClearML 的依赖安装流程。推理服务持有 GPU 资源；[ClearML services-mode](https://clear.ml/docs/latest/docs/clearml_agent/clearml_agent_services_mode/) 是轻量维护服务的模式，不能直接用其默认 services 队列承载 GPU 推理。

## 注册界面操作

1. 打开“注册新模板”，根据顶部说明准备镜像或批准的宿主机环境。可展开“从完整样例开始”，预览并载入六种配套样例；存在草稿时确认替换，载入不会自动注册或启动。
2. 填写名称与新版本，定义业务字段。启动命令按程序和固定参数逐项填写，动态参数在“业务命令参数映射”选择来源，再填写 --task、--server-url 或环境变量名。业务字段的映射与业务参数编辑器共享配置；重复目标会提示，原始命令数组保留在高级配置。
3. 在统一“目录挂载”区选择工作、输出、缓存、资产或批准目录，填写逻辑名和容器目标。节点负责解析真实路径。旧资产声明标为兼容声明，原 assets 占位符保持有效；资产和缓存只读。
4. 注册成功回到列表。“查看配置”按注册分组只读展示，复杂参数、挂载和补充配置均可查看；需要修改时复制新版本。配置查看不会修改模板。

完整样例、镜像构建和三任务 CS 演示见 [运行模板样例](../examples/runtime_templates/README.md)。示例是协议连通性回显，不是模型质量评测。
