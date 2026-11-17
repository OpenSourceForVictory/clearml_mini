## Requirements

### Requirement: Finished host executions remain recoverable after transient unit unload

When the systemd unit for a host execution is no longer found, the Worker SHALL recover an exited state only if a matching local launch marker says the execution started and a local exit record contains a valid exit code. It SHALL preserve the recorded exit code for normal completion handling.

#### Scenario: Unit unloaded after business exit

- **WHEN** the unit is absent and a matching started launch marker and valid exit record remain
- **THEN** the Worker reports the business exit code and completes the existing execution without restarting it

#### Scenario: Unit missing without confirmed exit

- **WHEN** the unit is absent and the exit record is missing or invalid
- **THEN** the Worker refuses to recreate that execution and reports the appropriate host-unit error
