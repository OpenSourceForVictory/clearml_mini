# 宿主机执行

管理员在机器详情 → 宿主机批准环境中配置命名环境，例如：

```json
{"simulation":{"python":"/opt/venv/bin/python","code_root":"/srv/simulation"}}
```

模板设置 `execution_mode=host`、`host_runtime.environment_name=simulation`；命令中的 python/python3 使用已批准解释器，脚本或原生可执行文件须位于 code_root，模块模式通过该目录的 PYTHONPATH 加载。业务依赖需事先安装，不自动 pip/conda 安装。环境名在模板中保持一致，绝对路径可以按机器不同。

节点需要 Worker v7、可用的 systemd 用户管理器、linger 与 cgroup v2。平台通过带版本的 `/admin/nodes/{id}/host-environments` 配置和 Worker 探测回执决定准入；缺失环境、旧 Worker 或过期报告不会启动 host 任务。修改环境不会改变已有执行快照。

宿主机环境回执有效期为 60 秒，仍要求 Worker 在线、配置版本匹配且该环境探测为就绪。检查失败会立即拒绝新任务，不会因延长回执窗口而被视作成功；执行中的任务保持原快照。

Worker 的 `execution_modes` 可设置 `["host"]`，只运行宿主机任务并跳过 Docker 存储探测；默认同时支持 Docker/host，Docker 镜像仍需批准。宿主机检查目录读写，不承诺资产只读隔离；这是可信业务程序运行方式，不能替代容器或多用户安全沙箱。

## 生命周期

每次执行使用 `mini-eval-exec-<execution ID>.service` 用户单元，systemd 限制 CPUQuota、MemoryMax、TasksMax 和 RuntimeMaxSec，并用 control-group 停止子进程树。CUDA_VISIBLE_DEVICES 指向分配的 GPU；显存仍为调度准入估算，不能硬限制 CUDA 进程显存。

Worker 租约看门狗在失联/停机时停止所属执行。恢复依据稳定执行 ID、单元描述和本地 journal，拒绝重复启动，不使用裸 PID 杀进程。启动标记先持久化再请求启动，遇到崩溃窗口宁可失败清理，也不重复运行不确定任务。

控制记录及最多各256KiB的 stdout/stderr 保留在本地恢复目录相邻 `.host` 路径，不放到 NFS。业务 input/work/output 沿用该节点工作目录，逐回合 JSONL、产物及 API 上报与 Docker 一致。业务产物受原有数量/大小限制，拒绝符号链接和非法路径。

```bash
systemctl --user list-units 'mini-eval-exec-*'
systemctl --user status mini-eval-exec-执行ID.service
```

正常停止应通过平台取消；不要直接删除 journal 或启动同名 systemd 单元。

## 验证

`HOST_EXECUTION_TEST=1 .venv/bin/python -m pytest backend/tests/test_host_backend.py -q` 会创建短生命周期用户单元，验证进程树停止、稳定身份恢复、拒绝重复启动、超时、限额与租约失联。

`scripts/smoke_host_runtime.py` 只接受隔离 API `http://127.0.0.1:18090`，配合 `EVAL_ADMIN_PASSWORD`、`STORAGE_TEST_HOST` 运行四组合样本（Docker/host × SDK命令/direct）；使用模拟 GPU 槽位，验证真实容器、宿主机进程、网络协议、逐回合事件和报告，不代表真实模型质量验收。

## 独立业务执行目录（Worker 0.15.0 / 协议 13）

解释器、代码根目录、业务执行目录和平台数据目录分别管理。节点批准环境示例：

```json
{"simulation":{"python":"/opt/conda/envs/eval/bin/python","code_root":"/data/projects/my_eval"}}
```

客户端或服务端模板在宿主机模式设置：

```json
{"execution_mode":"host","host_runtime":{"environment_name":"simulation","working_directory":".","environment":{},"overridable_env":[],"assets":[]},"command":["python","client.py","--config","configs/eval.yaml"]}
```

这相当于先进入 `/data/projects/my_eval`，再用 `/opt/conda/envs/eval/bin/python` 启动 client.py。平台通过 subprocess 的 cwd 启动，不拼接 shell。`working_directory` 也可为 `evaluation` 等子目录；绝对路径必须处于该节点批准 code_root 内。相对脚本路径基于所选业务目录解析，程序仍必须在批准根目录内。目录必须存在并可读/进入，拒绝 `..`、目录占位符和越界符号链接；跨节点推荐相对路径。

留空不发送该字段，保留旧行为：cwd 为本次任务独立 work，脚本路径从 code_root 解析。配置该字段的任务不能调度到协议低于 13 的节点；预检提示升级 Worker。模板查看和复制新版本保留此字段。

MINI_EVAL_WORK_DIR 和 MINI_EVAL_OUTPUT_DIR 不随业务 cwd 改变，分别用于执行临时数据与产物。多个任务可在同一个业务目录并发，业务程序不要把可变结果写入共享代码目录。Python SDK、SDK 命令包装、直接协议、普通命令及服务端共用目录解析。

验证用例在 `backend/tests/test_host_working_directory.py`：独立 venv Python 与业务目录、相对配置、四种入口、非法路径、缺失目录、越界链接、旧模板默认和旧 Worker 准入。验收范围是通用业务启动，不代表真实仿真资产或模型评测。

本次发布与逐项验证见 [独立执行目录验收](validation/host-working-directory/README.md)。六个在线节点已升级 Worker 0.15.0 / 协议 13。

## 通用业务命令（Worker 0.17.0 / 协议 14）

在客户端或服务端模板选择“命令解析方式 → 通用命令”。新模板默认使用此模式；旧模板及其复制版本保留兼容模式，可显式切换。

```json
{"execution_mode":"host","host_runtime":{"environment_name":"simulation","command_mode":"exec","working_directory":".","environment":{},"assets":[]},"command":["/bin/bash","run.sh"]}
```

通用模式使用“程序 + argv 参数 + 业务执行目录 + 环境变量”，不猜测解释器后的脚本位置：

| 启动程序和参数 | 用途 |
| --- | --- |
| `["python3", "-u", "app.py"]` | 使用节点配置的业务 Python，支持解释器选项 |
| `["/opt/venv/bin/python", "-m", "my_app"]` | 使用其他 Python 环境 |
| `["/bin/bash", "run.sh"]` | Bash 脚本 |
| `["./bin/evaluator", "--config", "config.json"]` | C/C++、Go、Rust 等编译后的可执行文件 |
| `["evaluator", "--config", "config.json"]` | 从最终业务 PATH 查找程序 |
| `["bash", "-lc", "./prepare && exec ./bin/server"]` | 显式启用 Shell 语法 |

平台不编译或安装业务依赖。原生程序必须匹配主机架构、动态加载器和共享库；脚本必须有解释器或有效 shebang。程序必须具备业务账号执行权限，代码与配置须可读取；错误出现在执行日志中。通用模式允许执行代码根目录之外的系统程序，但业务工作目录仍限制在批准代码根目录内，不构成安全沙箱。

宿主机通用模式留空业务目录时使用 code_root；兼容模式仍使用任务 work 目录。`python`/`python3` 别名沿用批准环境的 Python，绝对程序路径保持用户设置。平台入口使用 Worker 自身 Python；业务无需与它使用同一解释器，也不必导入平台 SDK。节点环境的基础 Python 探测要求仍保留，业务 Python/依赖不会自动安装。平台启动前/启动时检查目录与可执行程序，不能保证业务内部引用的所有文件都存在。

普通命令按退出码判定执行成功；逐回合 SDK 命令仍需写 MINI_EVAL_RESULT；直接评测协议与服务健康检查的约定不变。参数映射仍在执行前追加，环境映射参与最终 PATH 查找。不要把用户参数拼入 Shell 字符串；通过独立 argv 参数传入。

旧问题复现：兼容模式 `python3 -u app.py` 未配置业务目录时，Python 在任务 work 查找脚本并报文件不存在；`/bin/bash run.sh` 被旧 code_root 校验拒绝。迁移到通用模式后使用批准代码目录启动，二者可执行。旧模式不自动改写，避免改变存量任务行为。

Docker 的对应配置与验证见 [通用命令指南](generic-commands.md)。
