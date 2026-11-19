# Runner 补测与真实 CUDA 产物验证

本轮只增加测试与验证证据，没有修改或部署业务代码，没有升级节点。沿用已上线独立产物根和继承配置。

## 补测

`backend/tests/test_runner_entrypoint_contracts.py` 补充 HTTP/WS TCP 连通、HTTPS/WSS TLS、空DNS/受限地址/超时/拒绝、SDK包装器环境边界、捕获日志保留退出码、宿主机真实逐回合子进程、SDK参数冲突和资产/挂载展开。

入口文件42项单独通过；统一工具关联153通过、1跳过（显式真实Docker开关未启用）、2140未选择，Starlette弃用警告保留。完整项目分母不裁剪，全仓/功能组门槛仍失败。cs_entry行98.90%/分支97.06%，command_sdk行95.60%/分支96.67%，达到关键模块90%/80%要求，但不代表整个业务执行功能组达标。前端和平台真实进程未插桩，不纳入Python覆盖率。

复现：PYTEST_ADDOPTS=`--import-mode=importlib -k "runner_entrypoint_contracts or artifact_layout or command_sdk or host_episode or sdk_output_directories or shared_artifacts"`，运行 `.venv/bin/python -m scripts.test_coverage --output 自选目录`。

初次42项中4项失败：测试错误预期默认停止策略将参数ValueError转为EpisodeExecutionError，且预期exec模式未解析echo为绝对路径。修正断言后42项通过；没有更改生产异常处理、放宽参数冲突或绕过可执行文件解析。DNS/TLS失败分支使用mock，不声称现场网络故障测试。

## 可见真实 GPU 批次

[功能验证｜真实CUDA与SDK回合产物｜20261119](http://192.168.8.151:18000/#/cs/batches/f0517199-0ec0-42d0-9d73-b4e398988232)

平台实际调度 local4090-151，宿主机单进程原生SDK运行3回合。批准业务Python为 /opt/anaconda3/bin/python3，CUDA实际计算 sum(arange(1024)^2)=357389824，设备RTX4090；每回合写 episodes/N/output/cuda-result.json，通过context.artifact_path声明归属。三个文件API episode_ids分别为[1]、[2]、[3]。真实Chromium进入批次，按回合2筛选并预览GPU计算JSON通过。

任务SUCCEEDED且清理确认；临时业务环境配置恢复，测试模板停用，批次与文件保留。未运行机器人仿真或模型推理，不代表模型质量验收。证据result.json、smoke.log、browser.log；截图和完整API记录留在受限.runtime/artifact-gpu-sdk-20261119。

## 归属与 NPU 边界

本轮验证的是SDK明确声明的文件归属。普通命令/直接协议未声明文件仍是“未声明”，没有自动按目录名猜测；完整manifest方案未完成，OpenSpec 1.1/2.2/2.3/4.2保持未勾选。

1.95.69.29已通过严格主机校验SSH只读连通，业务Python/code_root存在，npu-smi列出8张卡。API目前只配置本机GPU产物共享映射，没有NPU对应共享路径；已请求用户提供节点产物根及API可读的对应路径。未在NPU提交新任务、修改目录权限或升级节点，不能把只读盘点算作NPU业务验收。OpenSpec5.2完整矩阵未完成。
