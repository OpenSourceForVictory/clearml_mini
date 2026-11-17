# 回连恢复验证

触发条件：Worker 注册前同步准备可选 Python 探测依赖，安装及锁等待可能超过 120 秒回连窗口；缺少探测能力又使严格能力比较拒绝完成安装。

修复：后台准备依赖，心跳更新能力；节点安装/升级验收豁免可选探测能力，但业务准入继续检查。执行器回连默认 300 秒、范围 30–1800；原操作支持用当前凭据继续验收，严格核对 revision/release，不重复 SSH 安装或轮换节点凭据。

针对性验证：第一组 75 项通过，随后新增错误分类/重启恢复和部署契约组 40 项通过，两组有重叠不相加。浏览器 8 项通过，验证续验不提交 SSH 凭据、原纳管流程和指南；TypeScript 与 Vite 构建通过，保留已有 500 kB chunk 提示。

失败证据：首轮全量测试的旧部署断言拒绝任何执行器环境变量，与新增超时配置冲突；改为准确白名单并保留无端口/无特权/无额外依赖检查。首轮覆盖率在修正后中止，不能用于通过声明。新增浏览器测试误用了不存在的机器配置按钮，修正为注册新机器后通过；一次重试因 cwd 路径错误中止。npm 不在 PATH，随后直接调用已安装 Node 的 tsc/vite 成功。日志保留 `.runtime/reconnect/`，不提交可能包含模拟凭据的原始日志。

边界：Python 使用隔离测试数据库、模拟 API/SSH/依赖安装，浏览器使用接口拦截。未访问用户节点，未执行真实 pip 下载验收或模型评测；用户提供的 HTTP getresponse 5 秒超时尚未验证恢复。前端与未合并独立进程不在 Python 覆盖率内。全量覆盖率结果如下。

最终全量：1301 项通过、100 项跳过、2 项警告，pytest 退出码 0，耗时 872.66 秒。所有既定目录与关键功能门槛通过，未改变门槛或排除代码。[完整覆盖率摘要](coverage-summary.md)：全部 Python 行覆盖率 90.02%、分支覆盖率 79.19%；纳管与升级 96.05% / 84.64%，业务执行与结果 93.53% / 82.42%，调度与资源 97.77% / 87.05%。

跳过项按原因汇总（不计为通过；原始 tests.xml 留存于 `.runtime/reconnect/coverage-final/`）：

- 2 项：dedicated PostgreSQL required
- 1 项：dedicated disposable orchestration migration database required
- 1 项：dedicated disposable resource migration database required
- 1 项：dedicated migration database required
- 1 项：disposable privileged mount namespace required
- 1 项：explicit disposable mount-namespace test required
- 24 项：opt-in real Docker matrix
- 8 项：real host/docker sample opt-in
- 8 项：real process/container execution opt-in
- 2 项：real systemd opt-in
- 22 项：requires Docker and delegated systemd
- 1 项：requires built mini-eval-template-examples:v1 image
- 1 项：requires isolated mini-eval-onboarding-test Compose project
- 2 项：requires local Docker image
- 12 项：requires local Docker image and real systemd opt-in
- 5 项：requires root, systemd and provisioned mini-eval test account
- 4 项：requires systemd user manager
- 2 项：separate disposable migration database required
- 1 项：set HOST_EXECUTION_TEST=1 for real systemd user units
- 1 项：set STORAGE_PROBE_TEST_IMAGE for real Docker probe
