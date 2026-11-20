# 直接适配器测试上下文修正

统一全量运行复现 test_result_outcomes.py 的6个 adapter_real_child 参数化用例失败：它们绕过 SDK.run，直接把 setup 阶段的 Context 传给 HostEpisodeClient，新增回合目录属性拒绝该上下文，因此尚未启动子进程就失败。

修正测试以 SDK.run 的真实约定构造带当前回合目录/ID的上下文，保留 good/missing/duplicate/null/fields/nonzero 全部真实子进程断言。生产 SDK.run 本已按回合构造独立 context，未为使测试通过而放宽生产校验。修正后6通过；原统一失败结果仍保留，不将单项重跑当作全量通过。对外 SDK 目录契约不变，参见 docs/task-input.md；Skill导航无需变更。
