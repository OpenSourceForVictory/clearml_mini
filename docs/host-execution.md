# 宿主机执行

## 登录环境与逐回合启动（Worker 协议 15）

宿主机模板可选 `host_runtime.environment_source=login`。Worker 在任务启动时以节点批准的业务账号运行非交互登录 shell，只读取导出的环境变量，再合并模板变量、允许的任务覆盖和节点 CANN 环境；平台分配的 GPU/NPU 可见设备变量最后写入。当前 SSH 会话、交互式 alias 与未导出的 shell 变量不会继承。登录 shell 超时、退出失败或输出无效时拒绝启动，错误不展示变量值。登录环境只传给业务子进程，平台 supervisor/runner 使用受控环境。模板不能切换业务账号。未填写此字段的旧模板维持原启动行为。各节点的 CANN 路径可不同，应在节点业务账号登录配置或批准环境中准备。

客户端宿主机模板还可选 `host_runtime.episode_launch=per_episode`。Worker 仍只创建一个受 systemd 管理的执行单元；其中的 runner 按集合回合数顺序启动前台业务子进程。每次传入单回合 `MINI_EVAL_INPUT`、独立 work/output 目录、`MINI_EVAL_EPISODE_ID` 和 `MINI_EVAL_EPISODE_SEED`。先生成本回合输入，再展开回合占位符并绑定任务业务参数与平台参数。当前子进程退出并完成协议校验后才启动下一回合；非零退出或无效结果停止后续回合。业务入口必须保持前台，后台派生后立即退出无法代表业务完成。普通命令以退出码记录每回合完成进度，但不计算评测成功率；直接协议每次必须输出单回合 final，由平台汇总。SDK 命令仍按原有每回合写 `MINI_EVAL_RESULT` 的方式运行。缺省 `whole_task`、Docker 执行及服务端模板维持原行为。 定向及节点验证边界见 [本次验证记录](validation/host-shell-episode-launch/README.md)。

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

客户端启动前连通性诊断：更新客户端 Worker 后，journal 中 `client connectivity passed/failed` 记录目标 origin（协议、主机、端口）、检查耗时、超时上限，成功记录方式（HEAD/TCP/TLS）与 HTTP 状态（TCP/TLS 为 -），失败记录异常类型和原因分类；任务错误保留 `client_service_unreachable` 并附带分类（timeout、dns、connection_refused、tls、http_response 等）。不记录路径、查询、凭据、响应正文或原始异常文本。HTTP(S) HEAD 收到 405 仍判定可达。WS 所有路径仅检查 TCP，WSS 检查 TCP/TLS 并使用系统默认信任校验证书；不再发送 HEAD 或 WebSocket 握手，不验证路径、业务认证、子协议或模型就绪。DNS 结果逐项校验并固定数值地址后连接；业务是否就绪仍以服务端就绪检查为准。此修复针对 WS 服务拒绝 HEAD 导致 unsupported http method: HEAD / ClientResponseError 400 的误判。默认 3 秒、不读取环境代理不变。查看客户端 `journalctl -u 'mini-eval-worker-*' --since '10 minutes ago'` 并对照失败时间。无需数据库迁移；仅更新平台 API 不会更新已纳管节点的 Worker。


## 回合出错后的处理

在管理设置 → 运行模板管理 → 客户端模板，复制新版本，选择宿主机执行和“每回合启动一次业务程序”，设置“回合出错后的处理”：

- **立即停止（默认）**：进程非零退出、缺少协议结果或结果非法时，记录 ERROR 并停止后续回合。旧模板保持此行为。
- **记录错误，继续后续回合**：例如3回合中第一回合退出1，记录该回合ERROR/退出码1，仍启动第二、第三回合。有任一执行错误时，任务最终仍为FAILED，不输出成功final；每回合退出码与任务整体退出码是不同信息。

正常进程输出有效协议结果 `success=false` 属于业务未成功，会记录 COMPLETED/业务失败并继续，不属于上述执行错误。普通命令按退出码判定；直接协议缺少结果即使退出0也记录ERROR；SDK命令仍要求合法的MINI_EVAL_RESULT。

API配置为 `host_runtime.episode_error_policy: "stop" | "continue"`。stop在保存时规范化为省略字段，避免旧Worker拒绝未知字段。continue仅用于宿主机per_episode客户端，覆盖普通命令、直接协议和SDK每回合命令。切回整个任务方式时保存stop；Docker和服务端生命周期不变。模板发布后需要用新版本重新提交，已有任务快照不会被修改。

初始化失败、取消、租约失效、总执行超时仍终止任务；continue不会重试错误回合，也不会自动修复共享服务状态。需要API、前端和Worker0.20.0（协议17），旧Worker被预检/调度/领取拦截。无需数据库迁移。

验证采用本机Python子进程（退出码1、缺少/非法结果、后续正常回合）、SDK/API回合记录及浏览器模板编辑；不代表真实GPU/NPU模型验收。按要求运行专项测试，未运行全量覆盖率门槛检查，前端未插桩。

本次验证：宿主机/SDK/回合记录专项67项通过；新增补充场景13项通过；策略规范化与旧Worker诊断复查3项通过（以上存在用例重叠，不合计）。保留Starlette弃用提示；前端构建保留大分块提示。

模板界面18项通过（包含策略复制和切换），TypeScript类型检查、前端构建和OpenSpec strict校验通过。

## 登录环境读取超时（Worker 0.20.2）

旧版本 `worker/host_identity.py` 的 `login_environment()` 固定只等 10 秒。用户确认账号登录初始化超过 10 秒时，Worker 在业务程序启动前报 `business account login environment could not be loaded`；这不是业务程序执行超时。0.20.2 默认改为 60 秒，超时明确报告 `business account login environment timed out after 60 seconds (user: <账号>)`，不输出变量值或 Shell 输出。

需要更长或更短时，由节点管理员在**相应每卡 Worker 使用的 JSON 配置顶层**添加一项，保留已有其他字段：

```json
"host_login_timeout_seconds": 90
```

只接受 1–300 的整数（不接受字符串、布尔值或 0）。界面暂无该设置，不是在模板 environment、节点 CANN environment 或平台 `.env` 中设置。未配置时按 60 秒，不需修改旧配置。仅登录环境模式读取此设置，显式环境和 Docker 不受影响。

通过 `systemctl show <Worker服务名> -p ExecStart -p WorkingDirectory` 找到正在运行的安装目录及 `--config` 路径。升级至 Worker 0.20.2，待该 Worker 空闲后 `sudo systemctl restart <Worker服务名>`；多卡节点按实际需要配置并重启各卡 Worker。只改 API 或任务总超时时间不会更新已安装 Worker。

平台 `STARTUP_TIMEOUT_SECONDS`（代码默认 120 秒）是独立的总启动等待预算。若将登录读取设置到接近或超过该预算，还需留出环境检查、CANN 初始化等耗时并核对部署的启动预算；本次修复不自动调整它，也不延长心跳租约或业务总执行时限。

验证包含默认/自定义/非法值、超时诊断不泄露输出、节点设置传递，以及真实子进程 Shell 等待 11 秒后成功的回归。它验证慢登录读取，不代表故障节点或真实模型已复测；导出函数导致无效环境的问题不在本次超时修复范围内。

本次专项验证：`test_host_shell_episode.py`、`test_host_backend_lifecycle.py`、`test_worker_docker_contracts.py` 共 91 passed，一条既有 Starlette 弃用警告；TypeScript 与 OpenSpec 严格校验通过。未运行全量测试或完整覆盖率统计，不据此宣称全仓覆盖率达标。代码修改不等于故障节点已更新或复测。

## 登录环境包含 Modules 导出函数（Worker 0.20.3）

触发条件：账号登录脚本导出 ml/module 等 Bash 函数，env 中出现 BASH_FUNC_ml%% / BASH_FUNC_module%%；旧 Worker 将其误判为非法变量名，业务启动前报 business account login environment is invalid。0.20.3 只跳过 BASH_FUNC_*%% 函数定义，保留登录脚本已设置的 PATH、LD_LIBRARY_PATH 等变量；其他非法名称、编码、普通变量长度及总环境大小校验继续有效。不会执行或继续传递导出函数；业务脚本如需再次调用 module load，须自行初始化 Modules。

升级故障节点 Worker 至 0.20.3，等待空闲后重启对应 Worker 服务并重新提交任务；多卡节点须更新对应服务实际使用的安装文件。临时补丁位于 worker/host_identity.py 的 login_environment：在 key,value=entry.split(b'=',1) 后、普通变量校验前跳过上述函数项。无需更改系统 Modules、模板或数据库，单独更新 API 不生效。

验证：真实 /bin/bash 导出 ml/module 的回归在修复前稳定复现 invalid，修复后读取成功；验证普通环境保留、16 KiB 边界、无效名称/编码及总量超限继续拒绝。仅相关测试和本地真实 Bash 验证，不代表故障节点已升级或完成模型业务验收，未重新统计全量目录覆盖率。

本次相关回归：test_host_shell_episode.py、test_host_backend_lifecycle.py、test_worker_docker_contracts.py 共 98 passed（一个既有 Starlette 弃用警告）；前端 TypeScript 检查及 OpenSpec strict 校验通过。失败与成功测试输出保留于本地 /tmp/login-functions-before.log、/tmp/login-functions-after.log。

## 登录环境名称兼容（Worker 0.20.4）

0.20.3 虽已过滤 Bash 函数，但仍按 Shell 标识符正则拒绝 MY-FLAG 等合法进程环境名称。0.20.4 接受非空 UTF-8 名称（包括中划线、点、空格和中文），通过 env 字典原样传递，不执行 Shell 插值。读取格式以 NUL 分隔条目、首个等号分隔名称和值；名称不能含这两个分隔符，值可包含等号。空名称、缺等号、无效编码、单值超过16 KiB或总量超过128 KiB仍拒绝，BASH_FUNC_*%%仍过滤。Shell 的 $变量 语法未改变，特殊名称需由程序通过环境 API 读取；模板字段校验规则不受本次修改影响。

节点需更新 Worker 至0.20.4，空闲时重启对应服务，再重新提交任务；无需数据库迁移或修改 API。专项测试覆盖真实子进程读取特殊名称/字面值，以及原有函数过滤、大小和编码失败路径。仅本地验证，未部署至故障节点，未重新统计全量覆盖率。

本次相关测试99项通过，含真实子进程环境读取、宿主机生命周期与Docker回归；前端TypeScript与OpenSpec strict通过。修复前特殊名称回归失败，证据保留在 /tmp/login-names-before.log，修复后结果在 /tmp/login-names-after.log。一个既有Starlette弃用警告；未运行全量覆盖率门槛验收。

## 启动准备期间心跳续租（Worker 0.20.5）

旧 Worker 初始心跳后同步读取登录环境、准备目录和创建执行，随后才发下一次心跳。若准备超过本地租约（默认30秒减2秒保护），即使登录读取允许60秒，也可能被 watchdog 停止。0.20.5 在初始握手成功后增加启动阶段专用续租线程，最多每2秒一次 started=false 心跳，覆盖等待启动锁和准备/启动过程；启动结束后停止该线程，恢复运行期心跳。

心跳成功才延长租约；503、网络错误在原租约内重试，阻塞或晚到响应不能恢复已失效执行。取消、停止和API拒绝在准备结束和启动前检查；已发起启动的执行由watchdog及清理路径停止。不会提前标记业务RUNNING，不改变任务全部回合共用总执行时限，也不绕过API STARTUP_TIMEOUT_SECONDS（默认120秒）的独立启动预算。无需为了慢登录一律放大LEASE_SECONDS。Docker创建/启动同样得到启动续租，但镜像、参数、设备和运行协议不变。

更新实际承接任务的Worker至0.20.5，空闲后重启再提交；无需数据库迁移或更新业务镜像。故障现场“批次第三个任务超时”尚未定位，此修复不证明该次故障由慢启动导致；运行中日志上传/控制面延迟及任务总时限仍须按实际日志诊断。

验证边界：本地真实Shell sleep超过初始租约、真实线程、故障注入API及模拟Docker/systemd边界验证续租、取消/失效、晚响应、恢复及连续三个任务；不等于真实GPU/NPU业务验收，未部署故障节点，也未运行全量覆盖率门槛统计。

本次验证：启动续租、执行恢复、服务执行、宿主机生命周期/登录环境、Docker契约及执行时限相关测试185 passed / 3 skipped（特权环境未开启），另补API启动预算测试1 passed。一个既有Starlette弃用警告；TypeScript和OpenSpec strict通过。初轮测试对晚响应的断言混淆“有效租约内启动后失租”和“失租后才启动”，失败输出保留 /tmp/startup-lease-tests.log；随后拆为两项测试分别断言停止已启动执行和禁止后续启动。最终输出 /tmp/startup-lease-all.log、/tmp/startup-lease-api.log。未跑全量覆盖率，不宣称达到目录覆盖率门槛。

Worker0.20.5已发布到实验平台，并升级local4090-151及ascend910b3-29，验证3个串行任务共9回合GPU与真实NPU服务；另用安装代码隔离验证35秒慢登录续租。用户其他故障平台未确认更新，验证范围见[发布验收](validation/startup-lease-release/README.md)。

Worker 0.21.0 的日志 v2 将宿主机诊断日志改为滚动窗口，stdout/stderr 均可持续查看；最终结果与回合事件独立保存。日志上传独立于心跳，退出补传有界。旧执行 journal 维持原模式，不能恢复此前已丢弃的日志。参见 [执行日志说明](execution-logs.md)。

## 启动标记与失败恢复（Worker 0.21.1）

Worker 的 `created=true` 表示已进入创建尝试，host-control/launch.json 的 `started=true` 表示已进入启动尝试，均不代表业务已运行或就绪。标记必须先于外部启动操作落盘，避免进程崩溃后重复执行。实际运行由 systemd 查询确认，就绪另由服务探测确认。不要将标记手动改回 false 或删除 journal 来绕过重复启动保护。

已复现的缺陷：首次启动失败、失败完成请求又因接口故障未送达，恢复时原错误被 `previously created host unit is missing` 覆盖。0.21.1 在停止/清理和失败上报之前保存首次失败请求，重试及 Worker 重启后重报原错误与退出码，不进入创建流程。若清理失败，保留记录等待恢复；已被平台接受的请求沿用 404/409 清理流程。

旧 journal 没有保存的错误无法补回。找不到 unit、没有可靠退出记录且本地标记已尝试创建或平台状态为 RUNNING 时，仍拒绝重复执行；提示附 execution_id、service_instance_id（旧 API 为 unknown）、unit、source。同名服务实例使用不同执行 ID；不同卡 Worker 的 journal、每次执行控制目录与 systemd unit 独立，清理只针对该执行。

需要更新并重启节点各卡 Worker 才生效，不能仅替换文件后保留旧进程。更新 API 后 claim 附带服务实例 ID，无数据库迁移。应先排空并按节点升级流程操作。修复原始启动问题后从平台重试产生新执行，不承诺自动恢复旧失败任务。专项验证与未覆盖边界见 [验证记录](validation/host-launch-failure-recovery/README.md)。


## 单回合参数映射修复（Worker 0.21.2）

旧版本可能在循环外把 `task.episodes` 或 `${episodes}` 展开成任务总回合数。例如平台计划10次启动，第一次业务却拿到 `--episodes 10`，业务内部重复执行而平台第1回合一直等待该进程退出。旧版本也可能提前固定种子及输入/工作/输出目录。这个触发条件已在本地复现，但不能据此断定所有“第1回合等待结果”都是同一原因。

宿主机显式“每回合启动”现在在每次启动前解析参数：`task.episodes` / `${episodes}` 为1，`task.seed` / `${seed}` 为当前回合种子，输入和工作/输出路径属于该回合。直接协议、普通命令和显式 per_episode SDK 命令一致；业务自定义参数不会按名称自动改写，手工写死的 `--episodes 10` 也不会被替换。外层保留总回合数和顺序事件编号。整任务、Docker 和服务端方式不变。

升级节点组件包中的 Worker 与 Runner 后，提交新任务验证，不需要创建宿主机 Runner 镜像或数据库迁移。现有进程不会热更新。第二回合运行时第一回合就应能显示结果，无须等整任务退出。界面日志按执行尝试汇总，业务内部打印 episode1 不代表平台回合编号。

验证及失败证据见 [参数映射回归记录](validation/episode-mapping-20261119/README.md)。

逐回合种子公式为 `(初始 seed + 回合编号 - 1) % 2147483648`。例如初始42的10回合依次为42至51；初始2147483647依次为2147483647、0、1……。`${seed}`、`task.seed` 映射、单回合输入及 `MINI_EVAL_EPISODE_SEED` 一致；字面量 `--seed 42` 不自动改变。

## 业务进程与线程上限（Worker 0.21.4）

宿主机和容器业务不再固定限制为256。节点每卡 Worker JSON 顶层可配置 `execution_pids_limit`：省略或 `null` 表示平台不添加 PID 上限，宿主机显式设置 `TasksMax=infinity`，容器显式设置 `--pids-limit=-1`；正整数表示每个执行实例的进程和线程总上限。不是环境变量，不放在模板 environment 中；当前界面没有此配置项。0、负数、布尔值和字符串均拒绝。

```json
{"execution_pids_limit": null}
```

如确有资源保护需求，可根据代表性业务的线程峰值和余量配置正整数，不能把示例数值当作统一推荐值。同一实例的 supervisor、runner、业务子进程和线程共享额度，多个服务实例分别计数；该值不参与CPU/内存调度。短时探测仍保留独立的32上限。

先升级所有相关 Worker 到0.21.4或以后，确认实际安装代码，再合并此字段到各卡现有配置（不要用示例覆盖整个JSON）。排空任务后重启相关Worker，重新提交任务；已创建容器/业务单元不会自动改变。无需数据库迁移，也不需要仅为此修复重建业务镜像。仅修改旧Worker JSON不会生效。

宿主机检查 `systemctl show mini-eval-exec-<执行ID>.service -p TasksMax -p EffectiveTasksMax -p TasksCurrent -p LimitNPROC`；用户级单元用业务账号的 `systemctl --user`。容器检查 `docker inspect <容器名> --format '{{.HostConfig.PidsLimit}}'`，必要时结合容器cgroup实际值。取消执行实例上限不等于系统无限制，父级cgroup、系统进程数和内存仍可能限制线程创建。

旧代码的256可能导致 `libgomp: Thread creation failed`；手工仅调大systemd值但未同步校验可能导致 `pids_limit_not_enforced`。新代码把显式PID值保存在launch快照中，systemd启动、Worker校验、supervisor校验统一使用快照。若仍失败，保留错误及实际限制，不删除执行标记强行重跑。验证范围见[PID上限回归记录](validation/execution-pids-20261119/README.md)。

0.21.4真机验证发现Docker运行时仍可能给容器systemd scope设置默认TasksMax：即使传入`--pids-limit=-1`，Docker inspect可能将PidsLimit规范化为null，而容器内pids.max仍是有限值。本次两台节点观察到76745/57402，不能只检查命令参数就宣称最终无限制；需要结合实际cgroup与父级值。本修复不自动修改Docker daemon或全机systemd默认配置。验证见[默认不限制发布记录](validation/unlimited-pids-20261119/README.md)。


## 程序执行与协议结果（Worker/Runner 0.21.5）

批次列表、任务行、回合详情及 JSON/CSV 报告分别展示程序执行和平台协议结果。退出码 0 表示业务程序正常结束；结果缺失、重复或格式错误仍是结果校验异常，不能当成业务成功。例：三个业务子进程均退出 0 但没有 final，显示程序正常结束 3 次、协议结果异常 3 回合。任务生命周期仍可能为 FAILED，表示需要处理异常，不代表三个业务子进程都异常退出。

逐回合宿主机直接协议必须在 stdout 单独一行输出唯一 final JSON；不加日志前缀，不写到 stderr：

```json
{"final":{"episode_count":1,"success_rate":1,"runtime":0.1}}
```

success_rate 按真实结果填写 0 或 1。SDK 包装命令则向 MINI_EVAL_RESULT 指定文件写入 `{"success":true}`（可带合法 metrics/artifacts），不能用 stdout final 替代这个文件。普通命令不要求协议结果。

已知错误以 `episode_result_missing_final`、`episode_result_multiple_final`、`episode_result_invalid_final`、`episode_result_missing_result_file`、`episode_result_invalid_result_file` 或 `episode_result_invalid_episode_result` 分类，并附固定修复提示，不把业务原文复制到事件。继续/停止策略保持不变；未知业务异常仍保留 stderr 堆栈。原 `CS client startup failed` 改为能覆盖运行和结果处理的提示。

业务子进程退出码与平台执行进程退出码分别显示。每回合启动按子进程统计；一次启动跑多回合只统计一次。原生 SDK 在同一进程内运行的回合没有独立退出码，显示不适用。历史单进程执行若只保存非零包装器退出码，无法确认是业务退出还是启动/结果处理失败，程序退出统计显示未上报，原包装器退出码仍保留供诊断；不伪造业务退出信息。

无需数据库迁移或提升 Worker 协议版本（仍为 17）。需要更新平台 API/前端及实际节点 Worker/Runner；业务镜像自带 Runner 的需重建镜像，不能只更新平台页面。此次代码验证不等于节点已升级或真实模型验收。

### 定位执行目录

任务详情和批次“排查任务”提供执行身份与目录区域：选择具体执行尝试后查看执行 ID，并复制节点已上报的实际目录。执行 ID 不等于任务 ID 或尝试记录 ID；重试不能沿用旧执行目录。无扫描记录显示“目录尚未上报”，而不是用当前配置推算；历史路径和当前节点配置可以不同。记录包含扫描时间、缺失或清理状态，不保证文件当前存在。程序 cwd 及每回合输入/产物目录的含义保持不变。
