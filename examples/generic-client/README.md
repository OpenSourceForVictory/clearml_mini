# 通用客户端协议样本

`business_episode.py` 只是确定性协议自检，不代表模型评测。SDK 命令模板设置 `integration_mode=sdk-command`，command 指向 Python 和此脚本；每次调用完成一个回合并写 MINI_EVAL_RESULT。

直接协议示例沿用 `runner/sdk_demo.py`，command 为 `python -m runner.sdk runner.sdk_demo:DemoClient`，设置 `integration_mode=direct`、`episode_reporting=true`。它读取实际分配的 endpoint 并访问配套 `scripts.cs_test_service`。

两种方式均需准备节点运行环境；Docker 镜像放入脚本，宿主机环境 code_root 指向脚本目录。详细契约见 ../../docs/runtime-templates.md。
