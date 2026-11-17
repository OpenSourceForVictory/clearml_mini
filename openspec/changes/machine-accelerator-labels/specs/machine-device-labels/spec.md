## ADDED Requirements
### Requirement: Neutral accelerator count labels
Machine management SHALL label device totals and table counts as accelerator devices for both client and server roles.
#### Scenario: NPU server is listed
- **WHEN** a server with eight NPU slots is displayed
- **THEN** its count is eight accelerator devices without a GPU-only label
#### Scenario: Empty or failed list
- **WHEN** the machine list is empty or fails to load
- **THEN** existing empty/error states remain and labels do not imply verified enrollment
