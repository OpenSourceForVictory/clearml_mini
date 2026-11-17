## ADDED Requirements
### Requirement: Automatic executor deployment
The default platform deployment SHALL start a healthy SSH executor without manual socket or host service configuration.
#### Scenario: Fresh deployment
- **WHEN** the platform starts with default settings
- **THEN** API connects to the bundled executor and authenticated administrators can register machines through the UI
#### Scenario: Legacy deployment
- **WHEN** an explicit host executor socket is configured
- **THEN** API retains that connection and the bundled executor does not overwrite its state or socket
### Requirement: Recoverable executor status
The executor SHALL persist operation records and the UI SHALL reflect connectivity.
#### Scenario: Restart
- **WHEN** the executor restarts
- **THEN** records remain and credentials expire without replaying installation
#### Scenario: Connectivity failure
- **WHEN** the executor is unreachable
- **THEN** the UI disables new checks, retains inputs and automatically detects reconnection
