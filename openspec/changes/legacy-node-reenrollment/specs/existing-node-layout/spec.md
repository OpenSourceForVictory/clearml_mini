## ADDED Requirements
### Requirement: Discover trusted existing system installations
The system SHALL discover an existing root-managed installation from its independent updater layout and preserve its paths, Worker units, device identities and business configuration.
#### Scenario: Eight-device custom installation
- **WHEN** a root re-enrollment check finds a complete trusted layout and matching services
- **THEN** it SHALL show the original application path and all eight devices and services without creating another identity
#### Scenario: Invalid or incomplete layout
- **WHEN** paths are untrusted, service mappings differ, a device is omitted or recovery records remain
- **THEN** it SHALL reject the operation before rotating credentials or replacing files
### Requirement: Bind installation to the checked layout
The system SHALL revalidate the checked layout and configuration before replacing an installation.
#### Scenario: Configuration changes after inspection
- **WHEN** configuration or effective service commands change after the plan is checked
- **THEN** installation SHALL reject the stale plan
### Requirement: Recover failed replacement
The system SHALL preserve original Worker settings and restore code and auxiliary component files on an ordinary replacement failure, keeping the node disabled.
#### Scenario: Auxiliary installation fails
- **WHEN** installing Agent or updater fails after code replacement
- **THEN** original code, Worker credentials and auxiliary files SHALL be restored with original ownership and permissions, without starting services or deleting recovery records
#### Scenario: Reconnect completes
- **WHEN** every original device and the Agent and updater reconnect with the expected release
- **THEN** the existing enrollment completion checks SHALL permit enabling the preserved node identity
