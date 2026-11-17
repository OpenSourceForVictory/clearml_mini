## ADDED Requirements
### Requirement: Resource role views
The resource page SHALL support all, client and server device views while preserving current permissions.
#### Scenario: Dual-role node
- **WHEN** a node has client and server roles
- **THEN** its devices appear in each role view and once in the all view
#### Scenario: Ordinary user
- **WHEN** an ordinary user selects a role
- **THEN** only aggregate device counts are returned and administrator details remain hidden
#### Scenario: Legacy device
- **WHEN** a device has no associated worker
- **THEN** it is classified as client for compatibility
