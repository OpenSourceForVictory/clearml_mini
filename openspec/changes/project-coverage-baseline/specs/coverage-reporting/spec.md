## ADDED Requirements
### Requirement: Coverage accounting
The project SHALL measure Python lines and branches including unexecuted source files and produce weighted directory and named functional summaries.
#### Scenario: Untested source
- **WHEN** a source file has no executed lines
- **THEN** it remains in its directory denominator and missing-line report
#### Scenario: Overlapping functions
- **WHEN** a file belongs to multiple functional groups
- **THEN** the global total counts the file once
### Requirement: Honest gates and reports
CI SHALL fail on test failures or directory coverage below its configured floor and retain reports; frontend and uninstrumented subprocess coverage SHALL NOT be reported as measured.
#### Scenario: Below baseline
- **WHEN** a directory line or branch percentage is below its floor
- **THEN** the check fails with that directory and threshold
#### Scenario: Failed tests
- **WHEN** pytest fails
- **THEN** diagnostic reports remain available but the overall command fails
### Requirement: Execution and admission regression coverage
Tests SHALL cover normal completion, cancellation, resource admission failure, recovery and transport faults without mutating production nodes.
#### Scenario: Transport outage during execution
- **WHEN** the execution API becomes unreachable
- **THEN** the Worker stops the execution and retains the journal for recovery
#### Scenario: Oversized onboarding control response
- **WHEN** the control API response exceeds 4 MiB
- **THEN** the executor preserves the response-limit rejection rather than classifying it as a network outage
#### Scenario: Operator scripts under test
- **WHEN** build and deployment scripts are tested with fault injection
- **THEN** command boundaries are isolated and successful unit tests are not described as real fleet deployment acceptance
#### Scenario: Current-version upgrade acceptance
- **WHEN** the isolated upgrade driver is run from a fresh checkout
- **THEN** it creates its temporary parent directory, derives the target from current Worker metadata and verifies a distinct temporary source version before launching workers
#### Scenario: Non-isolated two-host acceptance URL
- **WHEN** a two-host storage, image runtime or membership driver receives a non-isolated API URL
- **THEN** it rejects the run before starting its test listener or contacting nodes
#### Scenario: Real migration driver regression
- **WHEN** a dedicated disposable PostgreSQL server is explicitly configured for driver tests
- **THEN** each driver uses a separate temporary database and verifies historical backfill, downgrade guards and re-upgrade before deleting that database
