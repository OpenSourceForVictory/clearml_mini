# 调度完整验收样例

这是轻量 HTTP 回显推理服务，用来验证平台管理链路，不代表模型评测成绩。资产文件随样例提供，不需要 RoboTwin、checkpoint 或外部推理地址。

## 集合和模板

- `suite.json`：可从任务集合导入页面导入。3 个任务，每任务 2 回合，variant 分别 0/1/2，种子分别 1/2/3。
- `client-docker.json` / `client-host.json`：SDK 每回合命令，variant 映射到 `--variant`，label 映射到 `DEMO_LABEL`；sleep_seconds 控制任务持续时间，fail 用于注入业务异常。
- `server-docker.json` / `server-host.json`：model 映射到 `--model`，label 映射到环境变量；启动地址和端口由平台分配，客户端从标准输入获取匹配的服务地址。
- CPU 1 核、内存 1 GiB、单 GPU 独占，设备显存申请 0。本样例不调用 GPU 算子，但占用平台分配的设备以验证独占排队；不能作为 GPU 算力或模型正确性验收。
- 平台隔离每次执行的工作/输出目录；逻辑资产 `acceptance_data` 映射到各节点的 assets 目录。Docker 挂载至 `/sample-assets` 且只读，工作/输出分别 `/sample-work` 和 `/sample-output`；宿主机使用批准的逻辑目录，不承诺文件系统只读沙箱。

程序会核对业务参数与 argv/env 一致、资产 manifest 内容、实际 CPU/RAM 配额、服务端回显及执行身份；每回合生成 `episode-N.json`，返回真实 success/metrics/artifacts。缺少配置不会伪造通过。

## 环境准备

需要本次资源契约 API/Worker 版本，旧线上版本不能使用这些资源字段。Docker 镜像在仓库根目录构建：

```bash
docker build -f examples/scheduling_acceptance/Dockerfile -t mini-eval-scheduling-acceptance:v1 .
```

基础镜像 `mini-eval-client-base:1` 包含平台 runner；可通过 BASE_IMAGE 指定已经批准的兼容镜像。将镜像同步并批准到运行节点；资产放宿主机目录，在机器运行配置中映射逻辑名 `acceptance_data`。客户端和服务端节点都要有资产映射。

宿主机模式将 common.py/client.py/server.py 放入批准的代码目录，注册环境名 `acceptance_demo`，配置可用 Python 和 code_root；纳管资源限制检查必须通过。资产独立登记，不需放入镜像。

## 注册和运行

界面可分别载入对应模板 JSON 并注册，再导入 suite.json 并选择客户端模板。运行向导选择资源池、最大并行 2、平台启动服务，选择对应服务端模板。服务共享/独占分别测试，不需要手填服务地址。

也可使用同目录脚本。token 文件仅保存访问令牌，不是账号密码；运行记录不含令牌。默认只注册和预检，明确提供 `--submit-count 3` 才提交 3 批次：

```bash
python3 examples/scheduling_acceptance/register.py \
  --api http://127.0.0.1:18000 --token-file /path/to/token \
  --pool-id CLIENT_POOL --server-pool-id SERVER_POOL \
  --mode docker --service-mode shared --submit-count 3 \
  --output /tmp/scheduling-acceptance-run.json
```

每批 3 项任务/6 回合，三个批次共 9 项/18 回合。使用一个服务端槽位和两个客户端槽位，确认有任务在其他任务运行期间排队，随后推进。目录路径由节点映射解析，记录中保留 suite/template/batch ID，便于查看和清理。失败时保留注册记录，不盲目重复注册。

## 验收矩阵

| 场景 | 必须观察的证据 |
|---|---|
| 正常执行 | 9 个任务、18 回合成功，实际参数与映射匹配，服务绑定正确 |
| 资源不足 | 已运行任务存在时仍有 PENDING 排队项，释放后后续任务开始 |
| 服务共享 / 独占 | 共享复用同一服务；独占按绑定排队，不能错误连到其他批次 |
| 目录 | 宿主机逻辑路径可读；容器资产不可写；输出目录每执行隔离 |
| 业务失败 | 复制集合，将一项 fail=true；该项失败并清理，其他项继续 |
| 取消 | 将 sleep_seconds 增至 30，运行时取消批次；停止客户端、自动停止托管服务，确认清理后资源可再分配 |
| 资源硬限制 | 程序从内核读取 CPU/RAM 实际限额；不只核对提交表单 |

隔离 API 数据库上的真实 host/docker 执行测试：

```bash
ORCHESTRATION_EXECUTION_TEST=1 ORCHESTRATION_TEST_ADDRESS=本机可绑定地址 \
  .venv/bin/python -m pytest backend/tests/integration/test_scheduling_sample.py -q
```

该测试使用两个逻辑节点、真实进程/容器和实际模板注册、集合注册、编排提交与领取协议。它不等于跨物理节点验收；真实机群运行需要镜像、资产及新版 Worker 准备就绪后单独记录证据。
