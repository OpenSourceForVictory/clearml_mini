## ADDED Requirements

### Requirement: Distinct node report and resource sample freshness windows
The platform SHALL accept version-matching storage, container runtime, and host environment reports sampled within 60 seconds. It SHALL accept disk, GPU/NPU telemetry, and Agent host resource samples within 45 seconds. Administrator readiness views, preflight, scheduler admission, and claim-time checks SHALL use consistent windows.

#### Scenario: Ordinary probe delay
Given an online Worker with a matching, successful host environment report 30 seconds old, when a host task is preflighted, then the report is considered fresh.

#### Scenario: Expired or future report
Given a report or sample at or beyond its window, or timestamped in the future, when admission runs, then it is unavailable for a new task.

#### Scenario: Version or probe failure
Given a new desired configuration revision or a failed resource check, when admission runs, then the device remains unavailable regardless of the report timestamp.

#### Scenario: Independent lifecycle deadlines
Given a fresh environment report but an offline Worker or an expired execution lease, when admission or recovery runs, then the environment window does not override Worker liveness or execution lifecycle checks.
