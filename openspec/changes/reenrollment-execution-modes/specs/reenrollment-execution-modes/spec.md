## ADDED Requirements

### Requirement: Explicit re-enrollment execution mode selection
The SSH re-enrollment form SHALL default to preserving each existing device's execution modes. An administrator MAY choose host, container, or both instead. New-node enrollment SHALL reject the preserve choice.

#### Scenario: Preserve modes
Given an existing node with per-device execution modes, when an administrator leaves the execution environment at its default, then the plan displays the original modes and the installer retains them.

#### Scenario: Override modes
Given an existing node, when an administrator explicitly selects host, container, or both, then the plan displays that choice and the installer updates every original Worker configuration.

#### Scenario: Installation failure
Given an explicit mode change, when installation fails after configuration replacement, then the original modes are restored.

### Requirement: Probe effective runtime dependencies
The environment probe SHALL check host and Docker dependencies required by the modes that will take effect. After Worker registration, scheduling SHALL use the reported modes; other runtime readiness checks remain in force.

#### Scenario: Preserved host and container modes
Given a node whose existing devices include both host and container modes, when the administrator selects preserve, then both host resource limits and Docker availability are checked before installation.
