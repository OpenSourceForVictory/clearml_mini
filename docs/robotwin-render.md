# RoboTwin 轻量环境自检

`robotwin-render` 参考 [RoboTwin 官方渲染检查](https://github.com/RoboTwin-Platform/RoboTwin/blob/ea8b21121ebb3cd201ff5b3fe361944ac94eda3f/scripts/test_render.py)，固定 SAPIEN `3.0.0b1`、NumPy `1.26.4` 和 OpenCV `4.10.0.84`。保留官方检查的引擎、渲染器和场景初始化，去掉未使用的大型依赖，并增加真实图像和物理接触断言；并非直接运行完整 RoboTwin 任务。

每个回合让方块落到地面，使用 GPU 渲染 64×64 图像，核验图像形状、有限数值、颜色变化及落地高度。检查异常使 Runner 非零退出，平台显示失败。成功输出使用现有日志和指标协议，界面显示「环境检查通过率」。无需 checkpoint、机器人资产、网络、宿主机目录挂载或特权容器。

RoboTwin 的 MIT 授权文本保存在 `runner/ROBOTWIN_LICENSE` 并随镜像分发。

## 部署

在 GPU 节点准备驱动和 NVIDIA Container Toolkit，然后在仓库根目录构建：

宿主机必须有与当前 NVIDIA 驱动版本一致的图形/Vulkan 用户态库。仅有 headless 计算驱动时，`nvidia-smi` 可正常工作，但渲染仍会报 `failed to find a rendering device`；应补齐同版本、同驱动分支的图形库。R8000 使用 `580.178.04` server 分支，对应 `libnvidia-gl-580-server`，无需升级或替换内核驱动。

```bash
docker build -f runner/Dockerfile.robotwin-render -t mini-eval-runner:robotwin-render .
```

控制节点 `.env` 设置以下两项，Worker 配置的 `runner_image` 必须一致：

```dotenv
RUNNER_IMAGE=mini-eval-runner:robotwin-render
ROBOTWIN_RENDER_ENABLED=1
```

这个镜像同时包含 mock Runner。更新所有接入该控制节点的 Worker 镜像与配置后，运行 `python scripts/control.py up`。默认不开启环境自检，未部署时 API 拒绝提交。

Worker 设置 `mock: false`、正确的 `gpu_index`；资源池型号需匹配 `nvidia-smi`，建议每任务 2 CPU、4 GiB 内存。在网页选择「RoboTwin 环境自检」和 GPU 资源池，先提交 3 回合。API 参数如下：

```json
{"title":"RoboTwin 环境自检","benchmark":"robotwin-render","model_ref":"sapien-render","episodes":3,"pool_id":"资源池 ID"}
```

每次允许 1–100 回合；拒绝虚拟资源池和其他 model_ref。它验证 GPU 容器、SAPIEN 渲染和物理链路，不验证按铃等机械臂任务、不生成模型效果评分，也不替代真实多卡验收。

## NVIDIA runtime 模式

默认 Worker 使用 `--gpus device=<GPU UUID>`。也可在管理员配置里设置 `"gpu_runtime": "nvidia"`，使用已注册的 NVIDIA runtime 和 `NVIDIA_VISIBLE_DEVICES=<GPU UUID>`。两种方式均限制为所选物理 GPU。容器工具安装后，可在支持 runtime 配置热加载的 Docker 上加载配置，避免中断正在运行的服务；实际可用性以容器内检查为准。

```bash
docker run --rm --runtime=nvidia \
  -e NVIDIA_VISIBLE_DEVICES=GPU-填写实际UUID \
  mini-eval-runner:robotwin-render nvidia-smi -L
```

真实 GPU mock 故障恢复验收可在独立测试部署上运行：

```bash
SMOKE_GPU_INDEX=0 SMOKE_GPU_RUNTIME=nvidia python -m scripts.smoke_worker
```

此脚本还会通过 Docker 配置和容器内 `nvidia-smi` 核验 GPU 选择；其中 mock 成功率不属于真实机器人任务指标。
