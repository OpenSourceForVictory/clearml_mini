## Requirements

### Requirement: Host GPU task must match the local worker device

Before a physical GPU host task creates a launch marker, the Worker SHALL compare the task's assigned device index and stable UUID with its configured local GPU and current observed UUID. A mismatch or inability to verify SHALL reject the launch.

#### Scenario: Assigned GPU still matches

- **WHEN** task index and UUID match the Worker's current GPU
- **THEN** the Worker may create the task using the platform-assigned UUID

#### Scenario: GPU identity changed or task points elsewhere

- **WHEN** the index or UUID differs, is missing, or local inspection fails
- **THEN** the Worker rejects the task without creating a launch marker
