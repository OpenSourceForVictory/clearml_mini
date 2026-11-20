# 回合产物目录归属（0.21.12，2026-11-20）

触发条件：SDK 业务使用 episode_output_dir 写文件但没有逐文件返回 artifacts，或者逐回合普通命令生成输出。此前只有目录而没有归属登记，API 显示未声明，回合筛选漏掉这些文件。

## 实现与兼容

SDK 首次访问回合目录时写入 .mini-eval-artifacts（版本、回合编号）；宿主逐回合包装器复用该入口。嵌套 SDK 沿用父回合编号，旧公共 output_dir、显式 artifacts 路径与 setup 上下文不变。没有访问属性的旧业务不产生新目录。元数据独占发布，同目录不同回合不得覆盖；临时残留不显示为业务文件。发生写入故障只报告归属未确认，不改业务结果。

API 固定在已授权执行根内读目录元数据，不接受其中的任意路径、不根据 episode-1 名称猜测。显式逐文件协议引用优先，可同时引用多个回合。目录元数据由业务账号写入，是归属声明而非可信审计；任何内容都不能扩大文件读取权限。畸形、超大、符号链接、硬链接元数据不生效。宿主/容器旧上传采集器跳过此元数据，避免占用文件额度或上传内部文件。

SDK ZIP 增加 artifact_manifest.py 并同步下载包说明；节点组件版本 0.21.12、协议 17。无需数据库迁移；先更新 API，再更新实际使用的 Worker/Runner/SDK，旧式镜像内置 Runner 另行更新。**尚未部署**，当前线上本机仍为 0.21.11，历史执行不回填。

## 测试与失败证据

- 首轮 109 通过、2 失败、1 跳过。新增 reader 依赖使 `python -I artifact_reader.py` 找不到 runner，真实隔离子进程与普通账号读取失败。已修正为从固定安装位置加载单一辅助模块，不引入业务 cwd/import 路径。[保留首轮失败](baseline-failure.txt)。
- 旧 Docker 采集器新用例初次错误假设归档根名为 output，触发 invalid artifact archive root；使用已有 OUTPUT_DIR 常量修正夹具，产品路径校验未放宽。[夹具失败](fixture-failure.txt)。
- 统一 scripts.test_coverage 专项：271 通过、2 跳过、2074 未选择。新增 manifest 行 96.72%/分支 100%；SDK 93.79%/88.46%；读取器 100%/100%；API shared_artifacts 93.81%/84.85%。全仓分母保持原样，专项未覆盖全部功能，门槛失败保留，不能称全仓达标。[专项输出](targeted-tests.txt)、[覆盖率](targeted-coverage.md)。
- 扩展统一专项：523 通过、66 跳过、1758 未选择，0 测试失败；脚本因全仓门槛未过返回 1。manifest 96.72%/100%，SDK 95.17%/92.31%，reader 100%/100%，shared_artifacts 93.81%/84.85%，host_backend 97.49%/89.09%。host_episode 96.59%/78.12%、旧 artifacts 采集器 72.55%/57.14%，仍有未覆盖分支，不能声称全部关联模块达标。子进程内部未合并进覆盖率。[扩展输出](expanded-tests.txt)、[门槛报告](expanded-coverage.md)、[66 项跳过原因](skips.txt)。
- 浏览器批次/结果/产物回归 21 通过，TypeScript 和 Vite 构建通过，保留既有大 bundle 警告。浏览器使用模拟 API，不计 Python 覆盖率。[浏览器](browser.txt)、[构建](build.txt)。

## 实际 Docker 验证

[验证脚本](docker_check.py)使用 mini-eval-cs-test:1、实际非 root UID、只读挂载当前 Runner、持久绑定输出，运行原生 SDK 三回合，未返回显式 artifacts。容器删除后，节点上的结果文件仍存在；读取器识别回合 1/2/3，seed 为 42/43/44。[结果](docker-result.json)。临时目录已清理。

这验证真实容器进程/文件持久化和归属，不运行 GPU/NPU 算子、不做真实模型评测、不创建生产界面批次，也不是平台调度完整验收。宿主普通命令/直接协议正常及非零退出、SDK 异常保留、嵌套上下文、并发登记、越权与坏元数据由相关自动化用例覆盖。

完整产物功能仍有自定义目录、完整索引/清理与历史兼容联合验收待办；此切片不代表 task-result-artifacts 整体完成。
