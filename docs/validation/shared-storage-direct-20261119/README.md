# API 共享存储直读验证（2026-11-19）

本次范围是平台 output 目录的 API 共享存储直读，不含 Worker 转发、自定义目录或 SDK 自动目录归属。没有更新生产平台或节点。

## 实际容器和浏览器

使用已有 API 依赖镜像，当前源码只读挂载进独立容器，API 以 UID/GID 65534 运行。临时 SQLite 内构造隔离测试任务与目录登记；共享样例目录以 readonly bind 挂载，节点路径和容器路径不同。没有连接生产数据库。实际 Chromium 通过运行中的 API 授权和读取 WebM，未 mock 媒体响应。

- 视频为3秒160×120功能样例，Range 206，seek到第2秒；见 browser-media.json。
- 完整下载与源文件逐字节一致；SHA-256、文件大小见 container-boundaries.json。
- API写入只读挂载失败 errno=30；普通账号不可读文件返回403；错误存储marker返回503。
- 此为真实容器与HTTP读取验证，但不是现场NFS挂载、GPU/NPU业务执行或真实模型评测。隔离数据不出现在生产评测页面。未插桩容器/Python子进程与浏览器不计入统一Python覆盖率。

## 初次失败与修正

- 初次新增测试12项中2项失败：测试把Worker鉴权字典再次包装成token、标识文件父目录层级错误；修正fixture后12项全部通过。
- 扩展测试初次32通过、1失败：连接池断言引用了不存在的main.engine，修正为SessionLocal绑定引擎；没有删除连接池释放断言。
- 普通账号容器第一次无法进入工作区私有.runtime路径，改为单独只读绑定测试脚本；没有放宽.runtime权限。
- 环境没有npm命令，改用现有Node与项目本地tsc/vite；构建通过。保留Vite单包超过500kB提示，未调整警告阈值。
- 统一覆盖率的全仓目录/功能组门槛仍须如实记录；定向回归不能替代全量覆盖率。

具体回归数和覆盖率见本目录最终结果文件。数据库验证使用一次性PostgreSQL16.8和独立临时数据库，验证z629c01→a730d12、降级、再次升级、并发幂等登记与删除批次；旧RunArtifact正文保留，删除清理元数据不触碰共享文件。

## 最终结果

关联Python回归216通过、1跳过（显式opt-in Docker保留输出测试），额外节点离线断言修正后单项复跑通过；浏览器21通过。新增API模块行/分支：shared_artifacts 93.18%/82.00%，shared_file_io 95.18%/100.00%，shared_storage 93.88%/86.36%。这些模块达到90%/80%；全仓目录及功能组门槛未达标，见coverage-summary.md，不宣称本轮全量覆盖率通过。

复现统一统计：LOG_TEST_DATABASE_URL指向一次性数据库，PYTEST_ADDOPTS设置 --import-mode=importlib 与 -k "shared_artifact or artifact_reader or artifact_media or execution_locations or batch_deletion or process_log_migration or execution_launch_boundaries or host_backend_lifecycle or cs_protocol or worker_service_execution"，执行 .venv/bin/python -m scripts.test_coverage --output 自选目录。完整分母没有裁剪；本轮未执行全仓测试。
