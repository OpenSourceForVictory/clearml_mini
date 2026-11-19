# 重点测试用例索引

从当前测试文件提取函数名，帮助定位断言；参数化展开及结果以 pytest 报告为准。存在用例不等于本轮均已执行。

## [test_host_shell_episode.py](../../../backend/tests/test_host_shell_episode.py)

- `test_login_environment_reads_only_exported_values`
- `test_login_environment_rejects_invalid_output`
- `test_login_environment_timeout_has_no_values`
- `test_host_direct_starts_each_episode_with_params_and_aggregates`
- `test_host_direct_stops_after_bad_episode`
- `test_host_ordinary_command_repeats_without_business_result`
- `test_host_sdk_command_uses_business_environment_per_episode`
- `test_host_ordinary_single_episode_preserves_no_evaluation_metrics`
- `test_worker_to_host_episode_preserves_business_argv_env_and_input`
- `test_docker_entry_does_not_use_host_episode_adapter`
- `test_host_command_template_accepts_multiple_episodes_but_docker_does_not`
- `test_host_backend_keeps_login_values_in_business_only`
- `test_api_requires_all_host_command_episode_records_before_completion`
- `test_continue_direct_records_errors_and_attempts_all`
- `test_continue_ordinary_and_sdk_commands`
- `test_episode_policy_validation_and_old_worker_gate`
- `test_continue_does_not_swallow_setup_or_cancellation`
- `test_continue_all_episodes_error_without_success_final`
- `test_login_environment_passes_node_timeout`
- `test_login_environment_rejects_invalid_node_timeout`
- `test_login_environment_custom_timeout_redacts_output`
- `test_login_environment_real_shell_takes_more_than_old_limit`
- `test_login_environment_real_bash_exported_modules`
- `test_login_environment_function_filter_keeps_validation`
- `test_login_environment_function_filter_preserves_normal_names_and_boundary`
- `test_login_environment_special_names_reach_real_child`
- `test_per_episode_dynamic_mapping_uses_child_input`
- `test_existing_whole_task_mapping_preserves_total`
- `test_per_episode_environment_conflict_fails_before_business`

## [test_host_failure_recovery.py](../../../backend/tests/test_host_failure_recovery.py)

- `test_first_failure_survives_reload`
- `test_old_missing_journal_reports_identity`
- `test_two_card_workers_keep_independent_handles`
- `test_failed_exit_code_is_persisted_before_completion`

## [test_worker_startup_lease.py](../../../backend/tests/test_worker_startup_lease.py)

- `test_slow_real_shell_preparation_renews_without_starting_early`
- `test_startup_abort_never_launches_after_preparation`
- `test_transient_startup_renewal_recovers_within_lease`
- `test_three_sequential_executions_have_independent_startup_renewal`
- `test_late_startup_reply_does_not_revive_already_started_process`

## [test_sdk_output_directories.py](../../../backend/tests/test_sdk_output_directories.py)

- `test_episode_directory_context_and_legacy_root`
- `test_legacy_artifacts_are_not_rebased`
- `test_artifact_reference_rejects_escape`
- `test_artifact_reference_rejects_symlink_escape`
- `test_artifact_reference_relative_and_unicode`
- `test_episode_directory_refuses_symlink`
- `test_context_is_stable_after_business_exception`
- `test_episode_failure_continue_preserves_each_directory`
- `test_episode_context_preserves_input_paths_and_reporting`
- `test_sdk_entrypoint_receives_episode_context`

## [test_process_log_pipeline.py](../../../backend/tests/test_process_log_pipeline.py)

- `test_real_process_files_and_scoped_read`
- `test_store_rotation_execution_budget_and_uncommitted_tail`
- `test_missing_executable_marks_scope_incomplete`
- `test_host_per_episode_real_entry_and_protocol`
- `test_api_worker_file_mailbox_end_to_end`
- `test_host_backend_injects_log_snapshot`
- `test_store_rejects_symlink_and_hardlink_temp_without_touching_target`
- `test_parent_exits_without_waiting_for_descendant_pipe`
- `test_terminal_scope_discovery_and_slot_routing`
- `test_container_log_mount_identity_and_setup_failure`
- `test_process_log_launch_modes`
- `test_native_sdk_process_logs`

## [test_service_instance_claim_identity.py](../../../backend/tests/test_service_instance_claim_identity.py)

- `test_same_slot_two_service_claims`

## [test_worker_upgrades.py](../../../backend/tests/test_worker_upgrades.py)

- `test_upgrade_drains_all_work_and_checks_actual_reconnect`
- `test_unknown_updater_and_failed_rollback_cannot_resume`
- `test_release_is_immutable_and_permissions_apply`
- `test_archive_rejects_digest_and_traversal`
- `test_reserved_work_returns_to_queue_and_cancel_uses_revision`
- `test_upgrade_waits_for_every_registered_gpu`
- `test_registration_records_software_without_changing_identity`
- `test_release_audit_preserves_digest_with_bounded_target`
