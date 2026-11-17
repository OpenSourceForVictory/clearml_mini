## Decision
保持 systemd 独立任务单元和业务非 root 身份。Worker 构造基础环境、模板环境与节点批准的 CANN 配置后，在业务账号下以无登录配置的 Bash 加载已安装 CANN 的 `set_env.sh`，从子进程环境中只取 PATH、LD_LIBRARY_PATH、PYTHONPATH 和必要的 ASCEND 路径；平台继续最终设置 `ASCEND_RT_VISIBLE_DEVICES=0`。模板业务库目录保留在节点库目录之后。

优先使用节点显式 `ASCEND_HOME_PATH` 的脚本，再尝试业务账号标准安装位置和 `/usr/local/Ascend/cann`、`ascend-toolkit`。缺少脚本时保持既有显式配置行为；发现脚本但加载失败或没有库路径时，环境探测和任务启动均报可操作错误。脚本以业务账号运行、设置超时、丢弃脚本输出，避免把 root Worker 环境或任意脚本输出交给业务任务。

## Validation
测试实际临时脚本的变量合并与过滤、业务身份/超时参数、缺失和失败路径，以及任务启动清单与环境回执。29 节点只读验证 CANN 脚本加载后可打开 `libhccl.so`；这不代表真实模型推理成功。运行相关定向回归，不重新跑用户明确要求暂停的全量测试。
