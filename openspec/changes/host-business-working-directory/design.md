## Context
host 环境定义 python 与 code_root，任务 cwd 是执行独立 work。SDK 命令另行设置 cwd，必须与直接协议和服务同步。
## Decisions
working_directory 省略时不改变旧模板行为；`.` 表示 code_root，相对路径基于 code_root，绝对路径限定在该根目录内。路径存在、可访问且 resolve 后不能穿出根目录（包括符号链接）。非空配置时相对业务程序路径基于业务 cwd，以符合 cd 后执行语义；模块 PYTHONPATH 保留代码根目录和平台 runner。
Worker 解析出内部 _resolved_cwd，不允许用户通过业务参数覆盖。runner.cs_entry、command_sdk、service_entry 都读取它；MINI_EVAL_WORK_DIR/output 不变。协议版本 13、Worker 0.15.0，新字段只调度给支持版本；CPU 和加速设备走同一准入函数。
## Risks / Trade-offs
共享代码目录可产生并发写冲突：业务输出使用平台独立目录，不自动复制代码。批准根目录是可信业务边界，不是容器安全隔离。仅 API 升级不能赋予旧节点新能力，须升级执行节点。
## Validation
契约非法输入、缺失/越界/链接目录、旧模板默认、旧 Worker 准入；真实子进程使用独立 venv Python，读取 cwd 下相对文件，覆盖客户端 direct/SDK/普通命令和服务端；UI 保存/查看/复制；关联宿主机和协议回归。
