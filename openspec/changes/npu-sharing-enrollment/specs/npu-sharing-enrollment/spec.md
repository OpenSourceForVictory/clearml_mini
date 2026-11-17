## ADDED Requirements

### Requirement: GPU sharing and execution environment selection
The SSH enrollment form SHALL allow either or both host and container execution for the selected accelerator type. New installations SHALL verify every selected environment and persist both execution modes. GPU sharing SHALL be explicitly selectable and default to disabled on new nodes; re-enrollment SHALL preserve the previous value unless the administrator changes it. Existing GPU nodes without a local approval record SHALL retain their legacy pool-only behavior until changed.

#### Scenario: Dual environment GPU installation
Given a new GPU node with host and container selected and sharing enabled, when installed, then Docker and host prerequisites are checked and each selected Worker advertises both modes and shared GPU capabilities.

#### Scenario: GPU sharing disabled
Given a new GPU node with sharing disabled, when a shared GPU task is scheduled, then the node is ineligible even if its resource pool allows sharing.

### Requirement: New NPU installation approval
A new NPU installation SHALL default to no device sharing. The administrator MAY explicitly approve sharing in the SSH enrollment form. The installer SHALL persist that choice to each selected NPU Worker configuration; the Worker SHALL advertise shared execution only when local approval and backend checks succeed.

#### Scenario: Default installation
Given a new NPU node with no explicit sharing approval, when it is installed, then each Worker configuration has sharing disabled and no shared NPU backend is advertised.

#### Scenario: Explicit approval
Given a new NPU node with sharing explicitly enabled, when it is installed, then each selected Worker configuration records the approval and may advertise the corresponding shared NPU backend.

### Requirement: Existing NPU installation approval
Re-enrollment SHALL show the currently configured sharing value for each NPU and SHALL preserve all values by default. The administrator MAY explicitly enable or disable sharing for all enrolled NPU configurations. An explicit change SHALL require approved NPU runtime configurations and SHALL be included in the existing rollback transaction.

#### Scenario: Preserve or change
Given an existing NPU installation, when the administrator chooses preserve, then every original sharing value remains unchanged; when the administrator explicitly enables or disables sharing, then only that approval field and connection credentials are updated.

#### Scenario: Failed update
Given an explicit sharing change, when installation fails after configuration replacement, then the original sharing values, credentials, and code are restored.

### Requirement: Separation from resource pools
Node sharing approval SHALL be independent of resource pool sharing and task resource mode. The confirmation plan SHALL make the intended node approval visible without exposing node credentials or full configuration files.

#### Scenario: Plan review
Given an NPU re-enrollment check, when the plan is shown, then the administrator sees each existing sharing value and the selected action, without seeing the Worker token.
