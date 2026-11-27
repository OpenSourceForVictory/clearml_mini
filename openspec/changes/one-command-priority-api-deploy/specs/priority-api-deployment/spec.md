# Priority API Deployment

## Requirements

### Requirement: Existing platform upgrade

The deployment command SHALL preserve the specified Compose project, environment file and overlays. It SHALL refuse to stop the old API while active or unconfirmed executions exist. It SHALL verify a database backup before stopping the old API and SHALL migrate with the target image before starting the four-role API.

#### Scenario: Drained existing platform

- **WHEN** an administrator supplies the original project configuration and confirms draining
- **THEN** the command verifies there are no active or unconfirmed executions, saves and validates a backup, migrates to the target head, and starts healthy four-role services

#### Scenario: Active execution remains

- **WHEN** the database reports an active or unconfirmed execution
- **THEN** the command exits before stopping the old API

### Requirement: Failure boundary

The command SHALL stop on backup, database capacity, migration or health failure and SHALL NOT overwrite a previous backup or silently downgrade the database.

#### Scenario: Backup validation fails

- **WHEN** the backup archive cannot be validated
- **THEN** the incomplete file is removed and no API is stopped
