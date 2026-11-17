## ADDED Requirements

### Requirement: Visible sharing approval for new machines
The machine registration interface SHALL present accelerator type and device sharing approval before issuing credentials. SSH registration SHALL place the approval with the device selection before authentication fields. Manual registration SHALL default approval to disabled and include the selected accelerator type and corresponding sharing field in the downloaded node credential.

#### Scenario: New SSH registration
Given a new machine registration, when the administrator opens SSH registration, then the GPU or NPU sharing choice is visible near device selection and defaults to disabled.

#### Scenario: Manual NPU registration
Given manual registration with NPU sharing enabled, when the administrator issues and downloads the credential, then it contains `accelerator_type=npu` and `npu_sharing=enabled` for the installer.

#### Scenario: Changing a manual selection
Given an issued manual credential, when the administrator changes device type or sharing approval, then the existing credential retains its original selection and the interface requires a new credential to apply the change.
