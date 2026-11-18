# 宿主机执行

## 登录环境与逐回合启动（Worker 协议 15）

宿主机模板可选 `host_runtime.environment_source=login`。Worker 在任务启动时以节点批准的业务账号运行非交互登录 shell，只读取导出的环境变量，再合并模板变量、允许的任务覆盖和节点 CANN 环境；平台分配的 GPU/NPU 可见设备变量最后写入。当前 SSH 会话、交互式 alias 与未导出的 shell 变量不会继承。登录 shell 超时、退出失败或输出无效时拒绝启动，错误不展示变量值。登录环境只传给业务子进程，平台 supervisor/runner 使用受控环境。模板不能切换业务账号。未填写此字段的旧模板维持原启动行为。各节点的 CANN 路径可不同，应在节点业务账号登录配置或批准环境中准备。

客户端宿主机模板还可选 `host_runtime.episode_launch=per_episode`。Worker 仍只创建一个受 systemd 管理的执行单元；其中的 runner 按集合回合数顺序启动前台业务子进程。每次传入单回合 `MINI_EVAL_INPUT`、独立 work/output 目录、`MINI_EVAL_EPISODE_ID` 和 `MINI_EVAL_EPISODE_SEED`。任务业务参数与平台参数先绑定，再展开回合占位符。当前子进程退出并完成协议校验后才启动下一回合；非零退出或无效结果停止后续回合。业务入口必须保持前台，后台派生后立即退出无法代表业务完成。普通命令以退出码记录每回合完成进度，但不计算评测成功率；直接协议每次必须输出单回合 final，由平台汇总。SDK 命令仍按原有每回合写 `MINI_EVAL_RESULT` 的方式运行。缺省 `whole_task`、Docker 执行及服务端模板维持原行为。 定向及节点验证边界见 [本次验证记录](validation/host-shell-episode-launch/README.md)。

管理员在机器详情 → 宿主机批准环境中配置命名环境，例如：

```json
{"simulation":{"python":"/opt/venv/bin/python","code_root":"/srv/simulation"}}
```

模板设置 `execution_mode=host`、`host_runtime.environment_name=simulation`；命令中的 python/python3 使用已批准解释器，脚本或原生可执行文件须位于 code_root，模块模式通过该目录的 PYTHONPATH 加载。业务依赖需事先安装，不自动 pip/conda 安装。环境名在模板中保持一致，绝对路径可以按机器不同。宿主机任务直接读取节点上的 code_root，不需要为该目录另配 Docker 挂载；若路径来自 NFS 等共享存储，须先由操作系统在每台候选节点挂载，并确保业务账号可遍历和读取。

节点需要 Worker v7、可用的 systemd 用户管理器、linger 与 cgroup v2。平台通过带版本的 `/admin/nodes/{id}/host-environments` 配置和 Worker 探测回执决定准入；缺失环境、旧 Worker 或过期报告不会启动 host 任务。修改环境不会改变已有执行快照。

宿主机环境回执有效期为 60 秒，仍要求 Worker 在线、配置版本匹配且该环境探测为就绪。检查失败会立即拒绝新任务，不会因延长回执窗口而被视作成功；执行中的任务保持原快照。

Worker 的 `execution_modes` 可设置 `["host"]`，只运行宿主机任务并跳过 Docker 存储探测；默认同时支持 Docker/host，Docker 镜像仍需批准。宿主机检查目录读写，不承诺资产只读隔离；这是可信业务程序运行方式，不能替代容器或多用户安全沙箱。

## 生命周期

每次执行使用 `mini-eval-exec-<execution ID>.service` 用户单元，systemd 始终设置 TasksMax 和 RuntimeMaxSec；CPU/内存申请为正数时设置 CPUQuota、MemoryMax，未申请且资源池默认值为 0 时省略对应额度。control-group 用于停止子进程树。CUDA_VISIBLE_DEVICES 指向分配的 GPU；显存仍为调度准入估算，不能硬限制 CUDA 进程显存。

Worker 租约看门狗在失联/停机时停止所属执行。恢复依据稳定执行 ID、单元描述和本地 journal，拒绝重复启动，不使用裸 PID 杀进程。启动标记先持久化再请求启动，遇到崩溃窗口宁可失败清理，也不重复运行不确定任务。

宿主机任务很快结束、systemd 已卸载临时单元时，只要同一执行的启动标记及 supervisor 写下的有效 `exit.json` 仍在，Worker 就按记录的退出码继续结果上报与清理；不会重新启动程序。若缺少可信退出记录，显示 `previously created host unit is missing; refusing duplicate execution` 并使该执行失败。Docker 对应报错明确写 `Docker container`，同样不会原地重建。遇到此报错先看该执行更早的 Worker/业务日志，确认首次启动失败或单元被清理的原因；修复后通过平台重试生成新的执行尝试，不要手工删除 Worker journal 来强行重跑同一执行。

恢复路径的定向验证见[宿主机已结束单元恢复验证](validation/host-finished-unit-recovery/README.md)：覆盖有效、非零、缺失与损坏退出记录；模拟测试不能替代现场首次失败日志。

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

NPU 宿主机任务会在批准的业务账号下加载该节点已安装的 CANN `set_env.sh`，把 CANN 库路径加入业务环境；模板中配置的业务 `LD_LIBRARY_PATH` 目录也保留。标准安装无需逐卡填写 CANN 路径，非标准安装可由节点管理员显式批准 `ASCEND_HOME_PATH`。仅显式选择登录环境来源时才会读取批准业务账号的非交互登录 shell；Docker 任务仍依赖镜像内环境。脚本存在但加载失败会使宿主机环境探测显示不可用；缺少标准脚本时沿用旧的显式批准配置。

宿主机启动环境按平台基础值、模板业务变量、允许的任务覆盖、NPU 节点 CANN 环境、平台分配设备变量依次构造。Worker 会再次检查任务覆盖变量是否在模板允许列表中，并拒绝 `CUDA_VISIBLE_DEVICES`、`ASCEND_RT_VISIBLE_DEVICES`、`MINI_EVAL_*` 等保留变量；GPU 的 `CUDA_VISIBLE_DEVICES` 最后由平台写入分配卡 UUID。模板可设置业务所需的 `LD_LIBRARY_PATH`、`PATH` 等变量，无法借此选择其他卡。GPU 宿主机可见卡变量是程序选择提示，不是内核级设备隔离；多租户安全隔离仍需单独部署策略。

平台先在资源池选中设备槽位，再通过该槽位的 `claim` 响应把 `device_index`、`gpu_uuid` 和加速卡类型交给 Worker。物理 GPU 宿主机任务在创建启动记录前，Worker 用本机 `gpu_index` 查询当前 `nvidia-smi` UUID，并与下发编号和 UUID 核对；不一致或无法读取时拒绝启动。NPU 则复核卡号和稳定 identity，并通过 systemd 设备访问规则只开放分配的物理卡。出现 `assigned GPU index/UUID differs from worker device` 时，先核对节点上该卡的 `nvidia-smi --id=<设备编号> --query-gpu=uuid --format=csv,noheader` 结果、Worker 每卡配置与平台显示的设备；修复设备身份或重新纳管后再新建执行，不要通过改模板环境变量绕过。

宿主机业务默认用节点批准的普通账号运行。可信专用节点可按 [root 宿主机身份配置](root-managed-host.md) 在本地明确启用 root；模板和任务不能决定身份。root 只改变本机业务进程的文件访问身份；选择登录环境来源时可读取 root 的非交互登录 shell，但不能绕过 NFS root_squash。

留空不发送该字段，保留旧行为：cwd 为本次任务独立 work，脚本路径从 code_root 解析。配置该字段的任务不能调度到协议低于 13 的节点；预检提示升级 Worker。模板查看和复制新版本保留此字段。

MINI_EVAL_WORK_DIR 和 MINI_EVAL_OUTPUT_DIR 不随业务 cwd 改变，分别用于执行临时数据与产物。多个任务可在同一个业务目录并发，业务程序不要把可变结果写入共享代码目录。Python SDK、SDK 命令包装、直接协议、普通命令及服务端共用目录解析。

宿主机模板引用的逻辑资产需先在节点的容器环境/逻辑资产配置中映射实际路径，并列入 `host_runtime.assets`；Worker 直接把该节点路径交给业务程序，不创建容器挂载。模板的通用目录 `runtime.mounts` 仅用于 Docker。任务 work/output 由平台在节点工作目录下自动创建；批准 Python 与 code_root 不会替其他数据目录创建路径、挂载文件系统或授予权限。

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

如果启动前报 `business program must exist inside approved code root`，检查当前模板的命令解析方式、`host_runtime.working_directory` 和该节点批准环境的 `code_root`。兼容模式下，`["python","app.py"]` 要求文件存在于 code_root（或已指定的业务执行子目录）内；首项为 `/bin/bash` 等系统程序时也会被当作代码目录内的程序检查，应复制模板切换为“通用命令”，并用 `working_directory="."` 或正确子目录。Worker 会解析实际路径；越界符号链接和目录本身均不能当作业务文件。若代码来自共享存储，先在节点完成系统挂载，确保业务账号有遍历/读取权限，再重新检查环境并试跑。

Docker 的对应配置与验证见 [通用命令指南](generic-commands.md)。

宿主机服务运行期间若控制端因短时繁忙对执行心跳返回 HTTP 503，Worker 保留已启动的 systemd 单元并在现有租约期限内重试；租约仍由 watchdog 监督，持续不可用不会无限延长。其他 HTTP 拒绝仍按原有失败和清理流程处理。排查服务失败时同时查看平台服务日志、节点 `mini-eval-worker-*.service` 与对应 `mini-eval-exec-<执行ID>.service` 日志；业务输出出现不代表平台服务已达到就绪或已完成清理。2026-11-18 的真机验证中曾出现 ACL 程序已启动、随后心跳 503 导致服务失败，本修正针对该触发条件；自动化验证覆盖 503 重试与其他 HTTP 拒绝，不等同于业务模型评测。

本次真机宿主机 GPU/NPU 启动、失败路径和发布范围记录在 [2026-11-18 验证报告](validation/host-runtime-release-20261118/README.md)。

启动前首次执行心跳收到 HTTP 503 时，Worker 0.18.1 同样在原租约内等待重试；收到有效响应并处理取消后才创建或恢复业务单元。重试不会自行延长租约，停止或租约到期禁止启动，401 等其他拒绝仍按原流程处理。

客户端启动前连通性诊断：更新客户端 Worker 后，journal 中 `client connectivity passed/failed` 记录目标 origin（协议、主机、端口）、检查耗时、超时上限，成功记录方式与 HTTP 状态，失败记录异常类型和原因分类；任务错误保留 `client_service_unreachable` 并附带分类（timeout、dns、connection_refused、tls、websocket_handshake 等）。不记录路径、查询、凭据、响应正文或原始异常文本。HEAD 收到 405 仍判定可达，不代表业务就绪；非根路径 WebSocket 仍需握手成功或认证挑战 401/403。默认 3 秒、不读取环境代理不变。查看客户端 `journalctl -u 'mini-eval-worker-*' --since '10 minutes ago'` 并对照失败时间。无需数据库迁移；仅更新平台 API 不会更新已纳管节点的 Worker。
