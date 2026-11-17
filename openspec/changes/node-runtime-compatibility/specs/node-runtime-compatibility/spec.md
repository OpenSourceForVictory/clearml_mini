## ADDED Requirements
### Requirement: Kernel verified runtime admission
Root managed host execution SHALL support cgroup v1/v2 or mixed hierarchy only when CPU, memory and PID limits are verified.
#### Scenario: Unsupported limits
- **WHEN** a required controller is missing or ineffective
- **THEN** the node cannot advertise usable host execution
#### Scenario: Docker only
- **WHEN** a node selects Docker execution
- **THEN** host systemd resource probes are not required and a bounded container probe verifies limits
### Requirement: Automatic runner preparation
Docker onboarding SHALL prepare missing runner images after installation confirmation.
#### Scenario: Default image missing
- **WHEN** the configured default runner is absent and cannot be pulled
- **THEN** the trusted platform bundle is built on the target architecture before registration
#### Scenario: Custom image missing
- **WHEN** a custom runner cannot be pulled
- **THEN** preparation fails visibly without silently substituting another image
